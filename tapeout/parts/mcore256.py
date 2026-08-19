"""
mcore256 —— 自包含 256 位模乘引擎（旗舰核第二个零件）

任意 256 位模数 m 通用（要求 m > 2^255；secp256k1 的 p 与 n 均满足）：
模数常数由驱动器经 LD_C 预先载入 C 寄存器 —— RUN 前载 rc = 2^256 - m，
REDUCE 前载 m 本身。一颗电路服务 p、n 乃至任何 256 位模数。

算法：MSB-first 二进制模乘。每迭代 ACC = 2·ACC + b_i·A（mod m）：
  - ACC 驻留在 fadd64 子电路（REF）；自加倍经影子寄存器 S 回流实现
    （S 每拍顶插子电路 out_word —— 任何 op 下都等于 ACC 新顶字，零 mux 精确镜像）
  - 溢出补偿：cout=1 → 加 rc（C 寄存器），维持 ACC < 2^256 且 ≡ (mod m)；
    补偿可循环（2·ACC 的 +rc 自身可能再溢出，≤2 次必收敛）
  - A、C 为 256 位自由旋转环（纯连线）；B 逐位 rotl1 扫描
输入要求：A < m（驱动器自动 a % m）；B 任意 256 位（仅按位扫描）。
不变式：RUN 全程 ACC < 2^256 且 ACC ≡ 前缀积 (mod m)；
RUN 结束 ACC ≡ A·B (mod m)；REDUCE 后 ACC = A·B mod m（规范，< m）。

引脚（68 入 / 66 出）：
  输入：
    in_word[63:0]  pin 0..63   64 位字（LSW 先行）；LD_B 仅用 pin 0（逐位）
    cmd[2:0]       pin 64..66  命令（见下）
    start          pin 67      命令请求（仅在 ph==3 拍被接受）
  输出：
    out_word[63:0] pin 0..63   READ 拍流出 ACC（子电路 out_word 直通）
    busy           pin 64
    done           pin 65      RUN/REDUCE 完成（DONE 态 4 拍）

命令（cmd；6、7 保留）：
  0 LD_A    4 拍   in_word 流 A（LSW 先行）
  1 LD_B    256 拍 pin0 逐位流 B，**MSB 先行**（bit255 先进）
  2 LD_C    4 拍   in_word 流常数（LSW 先行）
  3 RUN     1 请求 256 迭代模乘（~1,550-2,600 拍），done 脉冲结束
  4 READ    4 拍   out_word 流出 ACC（LSW 先行）
  5 REDUCE  4~8 拍 规范约减：ACC -= C；若借位则 ACC += C 还原。done 脉冲结束

网格不变式：所有状态时长 ≡ 0 (mod 4)，状态迁移只在 ph==3 发生；
IDLE 停留任意久安全（退出必在 ph3）；A/C/S/ACC 字对齐因此永远保持。

驱动协议（精确拍控）：驱动器本地跟踪相位（复位后第 0 拍 ph=0），
只在 ph==3 拍发 start=1；载荷从下一拍开始流（4 拍或 256 拍）。

典型一次规范模乘（~1,600-2,900 拍）：
  LD_C(rc) → LD_A(a) → LD_B(b) → RUN → LD_C(m) → REDUCE → READ

门数控制：mux 全部采用共享反相 3-NAND 形式（sel_n 每选择信号只算一次）。
"""

from typing import List

from .. import Circuit, Signal

# 命令码
CMD_LD_A, CMD_LD_B, CMD_LD_C, CMD_RUN, CMD_READ, CMD_REDUCE = 0, 1, 2, 3, 4, 5

# FSM 状态
S_IDLE, S_LDA, S_LDB, S_LDC = 0, 1, 2, 3
S_SELF, S_RC1, S_BADD, S_RC2 = 4, 5, 6, 7
S_READ, S_RSUB, S_RADD, S_DONE = 8, 9, 10, 11
S_INIT = 12                     # RUN 前 4 拍 LOAD 0 清空 ACC


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_mcore256(child_cpu: bytes, child_cid: int) -> Circuit:
    """构造 mcore256。child_cpu/child_cid 指向一颗已流片的 fadd64。"""
    c = Circuit("mcore256", n_in=68, n_out=66)
    pins = c.input_signals()
    in_word = pins[0:64]
    cmd = pins[64:67]
    start = pins[67]
    Z = c.zero()

    # --- 状态寄存器（占位，闭环在末尾修补） ---
    st = c.latch_bus(4)          # FSM 状态
    ph = c.latch_bus(2)          # 4 拍网格相位（自由运行）
    it = c.latch_bus(8)          # LDB: 组计数 0..63；RUN: 迭代计数 0..255
    A = c.latch_bus(256)         # 被乘数环（自由旋转 64/拍）
    B = c.latch_bus(256)         # 乘数（rotl1 逐位扫描）
    C = c.latch_bus(256)         # 常数环 rc/m（自由旋转 64/拍）
    S = c.latch_bus(256)         # ACC 影子环（每拍顶插子电路 out_word）
    cmd_r = c.latch_bus(3)       # 已接受命令锁存

    # --- 状态译码（共享 4 个反相） ---
    st_n = [c.not_(st[i]) for i in range(4)]

    def st_eq(k: int) -> Signal:
        t = c.one()
        for i in range(4):
            t = c.and_(t, st[i] if (k >> i) & 1 else st_n[i])
        return t

    is_idle = st_eq(S_IDLE)
    is_lda = st_eq(S_LDA)
    is_ldb = st_eq(S_LDB)
    is_ldc = st_eq(S_LDC)
    is_self = st_eq(S_SELF)
    is_rc1 = st_eq(S_RC1)
    is_badd = st_eq(S_BADD)
    is_rc2 = st_eq(S_RC2)
    is_read = st_eq(S_READ)
    is_rsub = st_eq(S_RSUB)
    is_radd = st_eq(S_RADD)
    is_done = st_eq(S_DONE)
    is_init = st_eq(S_INIT)

    ph3 = c.and_(ph[0], ph[1])                     # 迁移闸：4 拍组末拍
    ph0 = c.and_(c.not_(ph[0]), c.not_(ph[1]))
    is_rc = c.or_(is_rc1, is_rc2)
    is_opnd = c.or_(is_self, is_badd)              # 操作数流（S 或 A）
    is_arith = c.or_(c.or_(is_opnd, is_rc), c.or_(is_rsub, is_radd))
    uses_C = c.or_(is_rc, c.or_(is_rsub, is_radd))

    b_bit = B[255]                                 # 当前乘数位（MSB-first）

    # --- 相位计数（自由运行含 IDLE，网格永不断） ---
    ph_d = [c.not_(ph[0]), c.xor(ph[0], ph[1])]

    # --- 迭代计数器（半加器链 +1） ---
    it_inc: List[Signal] = []
    cry = c.one()
    for i in range(8):
        it_inc.append(c.xor(it[i], cry))
        cry = c.and_(it[i], cry)

    it_n = [c.not_(it[i]) for i in range(8)]

    def it_eq(v: int) -> Signal:
        t = c.one()
        for i in range(8):
            t = c.and_(t, it[i] if (v >> i) & 1 else it_n[i])
        return t
    it_eq63 = it_eq(63)
    it_eq255 = it_eq(255)

    # --- 子电路 op/start（不依赖 REF 输出，无组合环） ---
    #   INIT                    → LOAD(00)（ACC 清零，RUN 前置）
    #   SELF/RC1/BADD/RC2/RADD → ADD(01)
    #   RSUB                   → SUB(10)
    #   READ/LD*/IDLE/DONE     → READ(11)
    is_sub = is_rsub
    is_pass = c.not_(c.or_(is_arith, c.or_(is_sub, is_init)))  # READ 类
    ch_op0 = c.or_(c.and_(is_arith, c.not_(is_sub)), is_pass)
    ch_op1 = c.or_(is_sub, is_pass)
    ch_start = c.and_(ph0, c.or_(c.or_(is_arith, is_read), is_init))

    # --- 子电路 in_word 选择（共享反相 3-NAND mux；INIT 时强制 0） ---
    is_badd_n = c.not_(is_badd)
    uses_C_n = c.not_(uses_C)
    is_init_n = c.not_(is_init)
    opnd = [_mux3(c, S[k], A[k], is_badd, is_badd_n) for k in range(64)]
    ch_in_w = [c.and_(_mux3(c, opnd[k], C[k], uses_C, uses_C_n), is_init_n)
               for k in range(64)]

    # --- REF fadd64（输入全部来自 LATCH 旧值/引脚，无组合环） ---
    ch_out = c.ref(child_cpu, child_cid, 67, 65,
                   ch_in_w + [ch_op0, ch_op1, ch_start])
    ch_out_word = ch_out[0:64]
    ch_cout = ch_out[64]

    # --- 迁移逻辑（仅 ph3 生效） ---
    # RC 循环补偿：2·ACC 或 ACC+A 的 +rc 补偿本身可能再溢出（罕见但存在），
    # RC 态 cout=1 时继续停留 RC 再加一次 rc（≤2 次必收敛：值 < 2^257 → -2m 后 < 2^256）
    go_rc1 = c.and_(c.or_(is_self, is_rc1), ch_cout)
    go_badd = c.and_(c.and_(c.or_(is_self, is_rc1), c.not_(ch_cout)), b_bit)
    go_rc2 = c.and_(c.or_(is_badd, is_rc2), ch_cout)
    iter_end = c.or_(c.and_(c.and_(c.or_(is_self, is_rc1), c.not_(ch_cout)),
                            c.not_(b_bit)),
                     c.and_(c.or_(is_badd, is_rc2), c.not_(ch_cout)))
    run_done = c.and_(iter_end, it_eq255)          # → DONE
    run_next = c.and_(iter_end, c.not_(it_eq255))  # → SELF（it++/B 扫描）
    ldb_stay = c.and_(is_ldb, c.not_(it_eq63))
    ldb_exit = c.and_(is_ldb, it_eq63)
    # REDUCE：RSUB 末拍借位（NOT cout）→ RADD 还原，否则 → DONE
    red_radd = c.and_(is_rsub, c.not_(ch_cout))
    red_done = c.and_(is_rsub, ch_cout)

    # 命令接受（IDLE 且 start）
    acc = c.and_(is_idle, start)
    acc_ph3 = c.and_(ph3, acc)
    cmd_n = [c.not_(cmd[i]) for i in range(3)]
    t_lda = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd_n[1], cmd_n[0])))
    t_ldb = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd_n[1], cmd[0])))
    t_ldc = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd[1], cmd_n[0])))
    t_run = c.and_(acc, c.and_(cmd_n[2], c.and_(cmd[1], cmd[0])))
    t_read = c.and_(acc, c.and_(cmd[2], c.and_(cmd_n[1], cmd_n[0])))
    t_red = c.and_(acc, c.and_(cmd[2], c.and_(cmd_n[1], cmd[0])))

    # next-state 汇集（默认保持现态；ph3 时改写）
    nxt_bits = [Z, Z, Z, Z]

    def merge(k: int, en: Signal):
        for i in range(4):
            if (k >> i) & 1:
                nxt_bits[i] = c.or_(nxt_bits[i], en)

    merge(S_LDA, t_lda)
    merge(S_LDB, t_ldb)
    merge(S_LDC, t_ldc)
    merge(S_INIT, t_run)                # RUN → INIT（清 ACC）→ SELF
    merge(S_READ, t_read)
    merge(S_RSUB, t_red)
    merge(S_IDLE, c.and_(is_idle, c.not_(start)))
    merge(S_IDLE, c.or_(c.or_(is_lda, is_ldc), is_read))
    merge(S_IDLE, ldb_exit)
    merge(S_LDB, ldb_stay)
    merge(S_SELF, is_init)
    merge(S_RC1, go_rc1)
    merge(S_BADD, go_badd)
    merge(S_RC2, go_rc2)
    merge(S_DONE, run_done)
    merge(S_SELF, run_next)
    merge(S_RADD, red_radd)
    merge(S_DONE, red_done)
    merge(S_DONE, is_radd)
    merge(S_IDLE, is_done)

    ph3_n = c.not_(ph3)
    st_d = [_mux3(c, nxt_bits[i], st[i], ph3_n, ph3) for i in range(4)]
    # mux3(a=新, b=旧, sel=ph3_n)：ph3=1 选新 ✓

    # --- it：命令接受清零；LDB 留态每组末 +1；RUN 迭代末 +1 ---
    it_clr = acc_ph3
    it_inc_en = c.and_(ph3, c.or_(run_next, ldb_stay))
    it_clr_n = c.not_(it_clr)
    it_inc_en_n = c.not_(it_inc_en)
    it_d = [c.and_(_mux3(c, it[i], it_inc[i], it_inc_en, it_inc_en_n), it_clr_n)
            for i in range(8)]

    # --- B：LDB 每拍插位旋转（pin0，bit255 先行）；RUN 迭代末 rotl1 ---
    b_rot = c.and_(ph3, run_next)
    b_act = c.or_(is_ldb, b_rot)
    b_act_n = c.not_(b_act)
    b_d = [Z] * 256
    for k in range(1, 256):
        b_d[k] = _mux3(c, B[k], B[k - 1], b_act, b_act_n)
    b_d[0] = c.mux(is_ldb, c.mux(b_rot, B[0], B[255]), in_word[0])

    # --- A/C 环：自由旋转 64/拍；载入态顶插 in_word ---
    is_lda_n = c.not_(is_lda)
    is_ldc_n = c.not_(is_ldc)
    a_d = [A[64 + k] for k in range(192)] + \
          [_mux3(c, A[k], in_word[k], is_lda, is_lda_n) for k in range(64)]
    c_d = [C[64 + k] for k in range(192)] + \
          [_mux3(c, C[k], in_word[k], is_ldc, is_ldc_n) for k in range(64)]

    # --- S 影子环：每拍顶插子电路 out_word（零 mux 精确镜像 ACC） ---
    s_d = [S[64 + k] for k in range(192)] + ch_out_word

    # --- cmd_r：命令接受时锁存 ---
    acc_ph3_n = c.not_(acc_ph3)
    cmd_d = [_mux3(c, cmd_r[i], cmd[i], acc_ph3, acc_ph3_n) for i in range(3)]

    # --- 输出封存（必须在最后） ---
    busy = c.not_(c.or_(is_idle, is_done))
    done = is_done
    c.sealed_outputs = c.seal_outputs(ch_out_word + [busy, done])

    # --- 闭环 ---
    c.drive_bus(st, st_d)
    c.drive_bus(ph, ph_d)
    c.drive_bus(it, it_d)
    c.drive_bus(A, a_d)
    c.drive_bus(B, b_d)
    c.drive_bus(C, c_d)
    c.drive_bus(S, s_d)
    c.drive_bus(cmd_r, cmd_d)
    return c


# ---------------------------------------------------------------------------
# 驱动辅助（测试与链上复放共用）
# ---------------------------------------------------------------------------

def make_input(word: int, cmd: int, start: bool) -> List[bool]:
    b = [bool((word >> k) & 1) for k in range(64)]
    b += [bool(cmd & 1), bool(cmd & 2), bool(cmd & 4), bool(start)]
    return b


def make_input_bit(bit: bool, cmd: int, start: bool) -> List[bool]:
    b = [False] * 68
    b[0] = bool(bit)
    b[64] = bool(cmd & 1)
    b[65] = bool(cmd & 2)
    b[66] = bool(cmd & 4)
    b[67] = bool(start)
    return b


def words_of(x: int) -> List[int]:
    return [(x >> (64 * i)) & ((1 << 64) - 1) for i in range(4)]


def bits_to_word64(bits) -> int:
    return sum(int(b) << k for k, b in enumerate(bits))


class MCoreDriver:
    """mcore256 精确拍控驱动器：本地跟踪相位，同序列可上链 beat 复放"""

    def __init__(self, sim):
        self.sim = sim
        self.ph = 0            # 本地相位跟踪（与电路 ph 一致）
        self.ticks = 0
        self.run_ticks = 0

    def _tick(self, ins: List[bool]) -> List[bool]:
        out = self.sim.beat(ins)
        self.ph = (self.ph + 1) % 4
        self.ticks += 1
        return out

    def _idle_to_ph3(self):
        while self.ph != 3:
            self._tick(make_input(0, 0, False))

    def _open(self, cmd: int):
        """在 ph==3 拍发 start=1（命令于下一拍起效）"""
        self._idle_to_ph3()
        self._tick(make_input(0, cmd, True))

    def load_a(self, a: int):
        self._open(CMD_LD_A)
        for w in words_of(a):
            self._tick(make_input(w, CMD_LD_A, False))

    def load_c(self, v: int):
        self._open(CMD_LD_C)
        for w in words_of(v):
            self._tick(make_input(w, CMD_LD_C, False))

    def load_b(self, b: int):
        self._open(CMD_LD_B)
        for i in range(255, -1, -1):          # MSB 先行
            self._tick(make_input_bit((b >> i) & 1, CMD_LD_B, False))

    def read(self) -> int:
        self._open(CMD_READ)
        ws = []
        for _ in range(4):
            out = self._tick(make_input(0, CMD_READ, False))
            ws.append(bits_to_word64(out[:64]))
        return sum(w << (64 * i) for i, w in enumerate(ws))

    def _wait_done(self):
        while True:
            out = self._tick(make_input(0, 0, False))
            if out[65]:                       # done 脉冲（4 拍）
                for _ in range(3):
                    self._tick(make_input(0, 0, False))
                return

    def run(self):
        t0 = self.ticks
        self._open(CMD_RUN)
        self._wait_done()
        self.run_ticks = self.ticks - t0

    def reduce(self):
        self._open(CMD_REDUCE)
        self._wait_done()

    def mulmod(self, a: int, b: int, m: int) -> int:
        """完整规范模乘：R = a·b mod m。
        A 操作数要求 < m（驱动器自动取模）；B 任意 256 位（仅按位扫描）。"""
        a = a % m
        rc = (1 << 256) - m
        self.load_c(rc)
        self.load_a(a)
        self.load_b(b)
        self.run()
        self.load_c(m)
        self.reduce()
        return self.read()
