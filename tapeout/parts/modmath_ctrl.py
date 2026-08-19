"""
modmath_ctrl —— ModMath Core 通用主控壳（ModMath Controller）

定位：通用密码学运算库（General Cryptographic Arithmetic Library）的统一调度壳。
纯组合电路（0 自有 LATCH），4 条 REF 缝合 4 颗已链上验证的 ECREC 零件：

  REF0 → fadd64b  (cid 6)   mod_add / mod_sub   字串行 256 位加减 ALU
  REF1 → mcore256 (cid 2)   mod_mul             自包含模乘引擎（内部再 REF cid1，深度2已验证）
  REF2 → piso256  (cid 3)   serialize           256 位并转串位流器
  REF3 → ringbusA (cid 4)   bus_transfer        5×256 位环总线寄存器堆
                               （ringbusB cid5 同网表第二实例备用）

为什么 mod_add 默认指 cid6 而不是 cid1：
  mcore256 内部 REF 的 fadd64 就是 cid1；若顶层 mod_add 也指 cid1，
  链上同 cid 状态共享会让 mod_mul 的 RUN 与挂起的 mod_add 互相污染 ACC。
  ECREC 旗舰主控（cid8 ecrecover_ctrl）的先例正是「顶层 ALU 用 fadd64b
  第二实例」。若驱动保证运算序列原子化（先 LOAD 后算），也可改指 cid1。

接口标准化（通用密码学运算接口，全部引脚 ≤255）：

  输入（74 针）：
    bus[63:0]   pin 0..63    共享 64 位字总线（全零件一致的 LSW 先行纪律；
                             mcore LD_B 位流走 bus[0]，MSB 先行）
    unit[1:0]   pin 64..65   运算单元选择：0=mod_add 1=mod_mul 2=serialize 3=bus_transfer
    ctrl[7:0]   pin 66..73   单元本地控制字（未选中单元自动零门控）：
      unit=0 fadd : ctrl[0]=op0 ctrl[1]=op1 ctrl[2]=start   （op: 0=LOAD 1=ADD 2=SUB 3=READ）
      unit=1 mcore: ctrl[0..2]=cmd ctrl[3]=start            （cmd: 0 LD_A 1 LD_B 2 LD_C 3 RUN 4 READ 5 REDUCE）
      unit=2 piso : ctrl[0]=load_en ctrl[1]=stream_en
      unit=3 ring : ctrl[0..2]=waddr ctrl[3]=we ctrl[4..6]=raddr

  输出（72 针）：
    out_word[63:0] pin 0..63   按 unit 四选一：fadd out_word / mcore out_word /
                               piso {bit0=bit_out, 其余 0} / ringbus rword
    status[7:0]    pin 64..71  bit0=fadd cout（unit=0）
                               bit1=mcore busy / bit2=mcore done（unit=1）
                               bit3..7 恒 0（保留）

安全性（未选中单元不受垃圾总线干扰）：
  - fadd 未选中时 op 强制 = READ（纯旋转，4 拍自复原），杜绝误 LOAD 污染 ACC
  - mcore 未选中时 start=0，IDLE 外状态不接受命令，in_word 被忽略
  - piso 未选中时 load_en=stream_en=0，空闲 rot64 自恢复
  - ringbus 未选中时 we=0 不写、raddr=0

网格纪律：壳无自有相位计数器，全局 4 拍网格由驱动器跟踪（beat 计数 %4）。
所有单元窗口均为 4 的倍数拍，运算边界对齐 ph0 即全系统字对齐。

资源估算：~770 NAND + 0 LATCH + 4 REF ≈ 6.3KB 网表（流片 gas ~2.7M，
远低于 BEP-652 上限 16.78M；OKX 单块 24,575B 上限内）。
"""

from typing import Dict, List

from .. import Circuit, Signal

# 运算单元选择码
UNIT_FADD, UNIT_MCORE, UNIT_PISO, UNIT_RING = 0, 1, 2, 3

# fadd64 操作码（与零件一致）
FA_LOAD, FA_ADD, FA_SUB, FA_READ = 0, 1, 2, 3

# mcore256 命令码（与零件一致）
MC_LD_A, MC_LD_B, MC_LD_C, MC_RUN, MC_READ, MC_REDUCE = 0, 1, 2, 3, 4, 5

# 默认 REF 目标（ECREC v2 处理器内 cid；fadd 用第二实例做状态隔离）
DEFAULT_REFS = dict(fadd=6, mcore=2, piso=3, ring=4)


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_modmath_ctrl(child_cpu: bytes,
                       cids: Dict[str, int] = DEFAULT_REFS) -> Circuit:
    """构造 ModMath Controller 主控壳。child_cpu 为零件所在处理器合约地址（20 字节）。"""
    c = Circuit("modmath_ctrl", n_in=74, n_out=72)
    pins = c.input_signals()
    bus = pins[0:64]
    unit = pins[64:66]
    ctrl = pins[66:74]
    Z = c.zero()
    O = c.one()

    # --- 单元选择译码（one-hot，共享反相） ---
    u0, u1 = unit[0], unit[1]
    u0_n, u1_n = c.not_(u0), c.not_(u1)
    sel_fadd = c.and_(u1_n, u0_n)      # 00
    sel_mcore = c.and_(u1_n, u0)       # 01
    sel_piso = c.and_(u1, u0_n)        # 10
    sel_ring = c.and_(u1, u0)          # 11
    sel_fadd_n = c.not_(sel_fadd)

    # --- REF0 fadd64b：未选中时默认 op=READ（保护 ACC），start 门控 ---
    fa_op0 = _mux3(c, O, ctrl[0], sel_fadd, sel_fadd_n)
    fa_op1 = _mux3(c, O, ctrl[1], sel_fadd, sel_fadd_n)
    fa_start = c.and_(sel_fadd, ctrl[2])
    fadd_out = c.ref(child_cpu, cids["fadd"], 67, 65,
                     bus + [fa_op0, fa_op1, fa_start])

    # --- REF1 mcore256：cmd/start 门控（未选中 cmd=0 start=0，安全） ---
    mc_cmd = [c.and_(sel_mcore, ctrl[i]) for i in range(3)]
    mc_start = c.and_(sel_mcore, ctrl[3])
    mcore_out = c.ref(child_cpu, cids["mcore"], 68, 66,
                      bus + mc_cmd + [mc_start])

    # --- REF2 piso256：load_en/stream_en 门控（未选中空闲自恢复） ---
    piso_load = c.and_(sel_piso, ctrl[0])
    piso_stream = c.and_(sel_piso, ctrl[1])
    piso_out = c.ref(child_cpu, cids["piso"], 66, 1,
                     bus + [piso_load, piso_stream])

    # --- REF3 ringbusA：waddr/we/raddr 门控（未选中 we=0 只读环0） ---
    rg_waddr = [c.and_(sel_ring, ctrl[i]) for i in range(3)]
    rg_we = c.and_(sel_ring, ctrl[3])
    rg_raddr = [c.and_(sel_ring, ctrl[4 + i]) for i in range(3)]
    ring_out = c.ref(child_cpu, cids["ring"], 71, 64,
                     bus + rg_waddr + [rg_we] + rg_raddr)

    # --- 输出总线四选一（两级 3-NAND mux 树） ---
    out_word: List[Signal] = []
    for k in range(64):
        lo = _mux3(c, fadd_out[k], mcore_out[k], u0, u0_n)
        hi_src = piso_out[0] if k == 0 else Z
        hi = _mux3(c, hi_src, ring_out[k], u0, u0_n)
        out_word.append(_mux3(c, lo, hi, u1, u1_n))

    # --- 状态位 ---
    st0 = c.and_(sel_fadd, fadd_out[64])     # fadd cout
    st1 = c.and_(sel_mcore, mcore_out[64])   # mcore busy
    st2 = c.and_(sel_mcore, mcore_out[65])   # mcore done
    status = [st0, st1, st2, Z, Z, Z, Z, Z]

    # --- 输出封存（协议：输出 = 最后 72 个信号） ---
    c.sealed_outputs = c.seal_outputs(out_word + status)
    return c


# ---------------------------------------------------------------------------
# 驱动辅助（本地对拍与链上 beat 复放共用同一序列）
# ---------------------------------------------------------------------------

def pack_input(bus_word: int, unit: int, ctrl: int) -> List[bool]:
    """74 位输入打包：bus[63:0] | unit[1:0] | ctrl[7:0]"""
    b = [bool((bus_word >> k) & 1) for k in range(64)]
    b += [bool(unit & 1), bool(unit >> 1)]
    b += [bool((ctrl >> k) & 1) for k in range(8)]
    return b


def words_of(x: int) -> List[int]:
    """256 位整数 -> 4 个 64 位字（LSW 先行）"""
    return [(x >> (64 * i)) & ((1 << 64) - 1) for i in range(4)]


def bits_to_word(bits) -> int:
    return sum(int(b) << k for k, b in enumerate(bits))


class ModMathDriver:
    """
    ModMath Core 标准化接口驱动器（全局 4 拍网格由 ticks%4 跟踪）。

    标准运算接口：
      mod_add(a, b, m)        → unit=0 (fadd64b)
      mod_sub(a, b, m)        → unit=0
      mod_mul(a, b, m)        → unit=1 (mcore256)，要求 m > 2^255
      serialize(data)         → unit=2 (piso256)，返回 256 位列表（MSB 先行）
      ring_write/read、bus_transfer(src, dst) → unit=3 (ringbusA)
    """

    def __init__(self, sim):
        self.sim = sim
        self.ticks = 0          # 全局 beat 计数 = 网格相位基准
        self.run_ticks = 0      # 最近一次 mod_mul RUN 的拍数（性能统计）

    # --- 底层 ---
    def _beat(self, bus_word: int, unit: int, ctrl: int) -> List[bool]:
        out = self.sim.beat(pack_input(bus_word, unit, ctrl))
        self.ticks += 1
        return out

    def idle(self, n: int):
        for _ in range(n):
            self._beat(0, UNIT_MCORE, 0)     # 全单元安全空闲态

    def _align_ph0(self):
        """对齐全局 4 拍网格（piso/ringbus 窗口纪律）"""
        while self.ticks % 4 != 0:
            self._beat(0, UNIT_MCORE, 0)

    # --- unit=0：fadd64b（mod_add / mod_sub） ---
    def _fadd_op(self, op: int, x: int):
        outs, cout = [], False
        for i, w in enumerate(words_of(x)):
            ctrl = op | ((1 if i == 0 else 0) << 2)
            out = self._beat(w, UNIT_FADD, ctrl)
            outs.append(bits_to_word(out[:64]))
            cout = bool(out[64])             # status[0] = cout（算术第 4 拍直通）
        return outs, cout

    def mod_add(self, a: int, b: int, m: int) -> int:
        """(a + b) mod m，完整编排：LOAD a → ADD b →(cout)+rc → SUB m →(bout)+m → READ"""
        rc = (1 << 256) - m
        self._fadd_op(FA_LOAD, a)
        _, cout = self._fadd_op(FA_ADD, b)
        if cout:
            self._fadd_op(FA_ADD, rc)
        _, cout = self._fadd_op(FA_SUB, m)
        if not cout:                          # 借位 = NOT cout
            self._fadd_op(FA_ADD, m)
        outs, _ = self._fadd_op(FA_READ, 0)
        return sum(w << (64 * i) for i, w in enumerate(outs))

    def mod_sub(self, a: int, b: int, m: int) -> int:
        self._fadd_op(FA_LOAD, a)
        _, cout = self._fadd_op(FA_SUB, b)
        if not cout:
            self._fadd_op(FA_ADD, m)
        outs, _ = self._fadd_op(FA_READ, 0)
        return sum(w << (64 * i) for i, w in enumerate(outs))

    # --- unit=1：mcore256（mod_mul） ---
    def _mc_open(self, cmd: int):
        """在 ph==3 拍发 start=1（mcore 命令接受闸）"""
        while self.ticks % 4 != 3:
            self._beat(0, UNIT_MCORE, 0)
        self._beat(0, UNIT_MCORE, cmd | 0x8)

    def _mc_wait_done(self):
        while True:
            out = self._beat(0, UNIT_MCORE, 0)
            if out[66]:                       # status[2] = done（4 拍脉冲）
                for _ in range(3):
                    self._beat(0, UNIT_MCORE, 0)
                return

    def mc_load_a(self, a: int):
        self._mc_open(MC_LD_A)
        for w in words_of(a):
            self._beat(w, UNIT_MCORE, MC_LD_A)

    def mc_load_c(self, v: int):
        self._mc_open(MC_LD_C)
        for w in words_of(v):
            self._beat(w, UNIT_MCORE, MC_LD_C)

    def mc_load_b(self, b: int):
        self._mc_open(MC_LD_B)
        for i in range(255, -1, -1):          # MSB 先行，走 bus[0]
            self._beat((b >> i) & 1, UNIT_MCORE, MC_LD_B)

    def mc_read(self) -> int:
        self._mc_open(MC_READ)
        ws = []
        for _ in range(4):
            out = self._beat(0, UNIT_MCORE, MC_READ)
            ws.append(bits_to_word(out[:64]))
        return sum(w << (64 * i) for i, w in enumerate(ws))

    def mod_mul(self, a: int, b: int, m: int) -> int:
        """完整规范模乘 R = a·b mod m（m > 2^255；A 操作数自动取模）"""
        a %= m
        rc = (1 << 256) - m
        t0 = self.ticks
        self.mc_load_c(rc)
        self.mc_load_a(a)
        self.mc_load_b(b)
        self._mc_open(MC_RUN)
        self._mc_wait_done()
        self.run_ticks = self.ticks - t0
        self.mc_load_c(m)
        self._mc_open(MC_REDUCE)
        self._mc_wait_done()
        return self.mc_read()

    # --- unit=2：piso256（serialize） ---
    def serialize(self, data: int) -> List[bool]:
        """装入 256 位并逐位流出（MSB 先行），返回 256 个 bool"""
        self._align_ph0()
        for w in words_of(data):
            self._beat(w, UNIT_PISO, 0x1)     # load_en
        bits = []
        for _ in range(256):
            out = self._beat(0, UNIT_PISO, 0x2)   # stream_en
            bits.append(bool(out[0]))
        return bits

    # --- unit=3：ringbusA（bus_transfer / 存储） ---
    def ring_write(self, ring: int, value: int):
        self._align_ph0()
        for w in words_of(value):
            self._beat(w, UNIT_RING, ring | 0x8)      # waddr | we<<3

    def ring_read(self, ring: int) -> int:
        self._align_ph0()
        ws = []
        for _ in range(4):
            out = self._beat(0, UNIT_RING, ring << 4)  # raddr<<4
            ws.append(bits_to_word(out[:64]))
        return sum(w << (64 * i) for i, w in enumerate(ws))

    def bus_transfer(self, src: int, dst: int) -> int:
        """环间拷贝：读 src（4 拍）→ 写 dst（4 拍），返回转运值"""
        v = self.ring_read(src)
        self.ring_write(dst, v)
        return v
