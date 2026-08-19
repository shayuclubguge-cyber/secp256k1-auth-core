"""
fairdice_ctrl —— FairDice Core 主控壳（链上可验证公平骰子）

定位：TapeOut 生态第一个「引用即挖矿」示范电路。输入签名，输出骰子，
每一步都引用已链上验证的底层密码学零件；壳本身只做调度 + 一个 3 位
mod-6 累加器，不重复造轮子。

REF×4（全部指向 ECREC v2 处理器 0xa3b6d912…47，嵌套深度 ≤3）：

  REF0 → ecrecover_ctrl (cid 8,  68→70)  AUTH   签名验证主控
  REF1 → kl_top         (cid 13, 65→66)  HASH   Keccak-256（种子生成）
  REF2 → fadd64b        (cid 6,  67→65)  MIX    种子 + 区块高度（mod 2^256）
  REF3 → piso256        (cid 3,  66→1)   STREAM 混合值位流（MSB 先行）

掷骰流程（驱动器编排，运算全部原子化、单元间转运走驱动器，同 ModMath
bus_transfer 纪律）：

  1. AUTH   结构校验宏程序：WRITE r,s → NE0 r / NE0 s /
            LTCHK r<n / LTCHK s<n / MUL 锚点 T2=r·s mod n；fail==0 才继续
            （完整 ECDSA 恢复 = 同一 AUTH 单元跑 compile_ecrecover 宏程序，
            本地分层验证：编译器影子执行对照 engine.ecrecover 黄金模型）
  2. HASH   seed = Keccak-256(r‖s)（64 字节，恰为 kl_top 原生输入规格）
  3. MIX    sum  = (seed + height) mod 2^256（fadd64 LOAD→ADD→READ，进位丢弃）
  4. STREAM piso256 流出 sum 的 256 位（MSB 先行）→ 壳内 mod-6 Horner
            累加器（每拍 acc ← (2·acc + bit) mod 6）；dice = acc + 1 ∈ 1..6

公平性论证：seed 是签名的 Keccak 哈希（签名者不可预选；矿工不可篡改），
height 逐块递增不可回拨；(sum) mod 6 的偏差 ~2^-256 量级（可忽略）。
任何人可用 seed（随骰子一并输出，可验证凭证）离线复算骰子点数。

接口标准化（全部引脚 ≤255）：

  输入（74 针）：
    bus[63:0]   pin 0..63    共享 64 位字总线（全零件一致的 LSW 先行纪律）
    unit[1:0]   pin 64..65   单元选择：0=AUTH 1=HASH 2=MIX 3=STREAM
    ctrl[7:0]   pin 66..73   单元本地控制字（未选中单元自动零门控）：
      unit=0 AUTH  : ctrl[0..2]=cmd ctrl[3]=start   （与 ecrecover_ctrl 一致）
      unit=1 HASH  : ctrl[0]=start                  （与 kl_top 一致）
      unit=2 MIX   : ctrl[0..1]=op ctrl[2]=start    （op: 0=LOAD 1=ADD 2=SUB 3=READ）
      unit=3 STREAM: ctrl[0]=load_en ctrl[1]=stream_en ctrl[2]=acc_clear

  输出（83 针）：
    out_word[63:0] pin 0..63   按 unit 四选一：AUTH/HASH/MIX 的 out_word；
                               STREAM 时 {bit0=piso bit_out, 其余 0}
    status[15:0]   pin 64..79  bit0=auth busy / bit1=auth done / bit2=auth fail
                               bit3=auth fadd_cout
                               bit4=auth mcore_busy / bit5=auth mcore_done
                               bit6=hash busy / bit7=hash done
                               bit8=mix cout / bit9=stream bit_out
                               bit10..15 恒 0（保留）
    dice[2:0]      pin 80..82  acc+1（二进制 001..110 = 点数 1..6；
                               256 拍位流结束后稳定有效）

安全性（未选中单元不受垃圾总线干扰，与 modmath_ctrl 同款纪律）：
  - AUTH 未选中：cmd=0 start=0（IDLE 忽略总线）
  - HASH 未选中：start=0（IDLE 忽略总线；哈希一旦开始必须跑完，原子纪律）
  - MIX  未选中：op 强制 = READ（纯旋转，保护 ACC）
  - STREAM 未选中：load_en=stream_en=0（空闲 rot64 自恢复），acc 保持

网格纪律：壳无自有相位计数器；ecrecover_ctrl 内部 ph 自由运行，
驱动器以全局 beat 计数 %4 跟踪；piso256 装入窗口 ph0 对齐、
装入→流出间隔 ≡0 (mod 4)；全部运算边界对齐 ph0 即全系统字对齐。

资源实测（见 tests/test_fairdice_core.py）：~630 NAND + 3 LATCH + 4 REF
≈ 5.4KB 网表（流片 gas ~2.3M，远低于 BEP-652 上限 16.78M；
OKX 单块 24,575B 上限内；晶体管 <3,000 目标内）。
"""

from typing import Dict, List

from .. import Circuit, Signal

# 单元选择码
UNIT_AUTH, UNIT_HASH, UNIT_MIX, UNIT_STREAM = 0, 1, 2, 3

# ecrecover_ctrl 命令码 / 宏指令操作码（与零件一致）
CMD_WRITE, CMD_EXEC, CMD_READ = 1, 2, 3
OP_NOP, OP_MOV, OP_EQ0, OP_NE0, OP_LTCHK, OP_ADD, OP_SUB, OP_MUL = range(8)
PIN, NULL = 14, 15

# fadd64 操作码（与零件一致）
FA_LOAD, FA_ADD, FA_SUB, FA_READ = 0, 1, 2, 3

# 默认 REF 目标（ECREC v2 处理器内 cid）
DEFAULT_REFS = dict(auth=8, hash=13, mix=6, stream=3)


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_fairdice_ctrl(child_cpu: bytes,
                        cids: Dict[str, int] = DEFAULT_REFS) -> Circuit:
    """构造 FairDice Controller 主控壳。child_cpu 为零件所在处理器合约地址（20 字节）。"""
    c = Circuit("fairdice_ctrl", n_in=74, n_out=83)
    pins = c.input_signals()
    bus = pins[0:64]
    unit = pins[64:66]
    ctrl = pins[66:74]
    Z = c.zero()
    O = c.one()

    # --- 单元选择译码（one-hot，共享反相） ---
    u0, u1 = unit[0], unit[1]
    u0_n, u1_n = c.not_(u0), c.not_(u1)
    sel_auth = c.and_(u1_n, u0_n)       # 00
    sel_hash = c.and_(u1_n, u0)         # 01
    sel_mix = c.and_(u1, u0_n)          # 10
    sel_stream = c.and_(u1, u0)         # 11
    sel_mix_n = c.not_(sel_mix)

    # --- REF0 ecrecover_ctrl：cmd/start 门控（未选中 IDLE 安全） ---
    au_cmd = [c.and_(sel_auth, ctrl[i]) for i in range(3)]
    au_start = c.and_(sel_auth, ctrl[3])
    auth_out = c.ref(child_cpu, cids["auth"], 68, 70,
                     bus + au_cmd + [au_start])

    # --- REF1 kl_top：start 门控（未选中 IDLE 安全） ---
    ha_start = c.and_(sel_hash, ctrl[0])
    hash_out = c.ref(child_cpu, cids["hash"], 65, 66,
                     bus + [ha_start])

    # --- REF2 fadd64b：未选中时默认 op=READ（保护 ACC），start 门控 ---
    mx_op0 = _mux3(c, O, ctrl[0], sel_mix, sel_mix_n)
    mx_op1 = _mux3(c, O, ctrl[1], sel_mix, sel_mix_n)
    mx_start = c.and_(sel_mix, ctrl[2])
    mix_out = c.ref(child_cpu, cids["mix"], 67, 65,
                    bus + [mx_op0, mx_op1, mx_start])

    # --- REF3 piso256：load_en/stream_en 门控（未选中空闲自恢复） ---
    st_load = c.and_(sel_stream, ctrl[0])
    st_stream = c.and_(sel_stream, ctrl[1])
    piso_out = c.ref(child_cpu, cids["stream"], 66, 1,
                     bus + [st_load, st_stream])
    bit_in = piso_out[0]

    # --- mod-6 Horner 累加器（壳唯一自有逻辑）：acc ← (2·acc + bit) mod 6 ---
    # t = 2·acc + bit ∈ {0..11}；ge6 = a2 | (a1·a0)；
    # t ≥ 6 时 acc' = t − 6 = (t + 2) mod 8 = (a1⊕a0, ¬a0, bit)，否则 (a1, a0, bit)
    acc = c.latch_bus(3)
    a0, a1, a2 = acc[0], acc[1], acc[2]
    ge6 = c.or_(a2, c.and_(a1, a0))
    ge6_n = c.not_(ge6)
    nxt0 = bit_in                                   # 两种路径 bit0 都是输入位
    nxt1 = _mux3(c, a0, c.not_(a0), ge6, ge6_n)
    nxt2 = _mux3(c, a1, c.xor(a1, a0), ge6, ge6_n)
    shift_en = st_stream                            # 仅在 STREAM 流出拍移位
    shift_n = c.not_(shift_en)
    acc_clear = c.and_(sel_stream, ctrl[2])
    clear_n = c.not_(acc_clear)
    for i, nxt in enumerate((nxt0, nxt1, nxt2)):
        held = _mux3(c, acc[i], nxt, shift_en, shift_n)
        c.drive_latch(acc[i], c.and_(held, clear_n))

    # --- dice = acc + 1（组合，001..110 = 点数 1..6） ---
    d0 = c.not_(a0)
    d1 = c.xor(a1, a0)
    d2 = c.xor(a2, c.and_(a1, a0))

    # --- 输出总线四选一（两级 3-NAND mux 树） ---
    out_word: List[Signal] = []
    for k in range(64):
        lo = _mux3(c, auth_out[k], hash_out[k], u0, u0_n)
        hi_src = _mux3(c, mix_out[k], bit_in if k == 0 else Z, u0, u0_n)
        out_word.append(_mux3(c, lo, hi_src, u1, u1_n))

    # --- 状态位（ecrecover_ctrl 全部 6 个状态信号都要暴露：MUL 流程依赖
    #     mcore_busy/mcore_done 拍级观测） ---
    st = [c.and_(sel_auth, auth_out[64]),     # bit0  auth busy
          c.and_(sel_auth, auth_out[65]),     # bit1  auth done
          c.and_(sel_auth, auth_out[66]),     # bit2  auth fail
          c.and_(sel_auth, auth_out[67]),     # bit3  auth fadd_cout
          c.and_(sel_auth, auth_out[68]),     # bit4  auth mcore_busy
          c.and_(sel_auth, auth_out[69]),     # bit5  auth mcore_done
          c.and_(sel_hash, hash_out[64]),     # bit6  hash busy
          c.and_(sel_hash, hash_out[65]),     # bit7  hash done
          c.and_(sel_mix, mix_out[64]),       # bit8  mix cout
          c.and_(sel_stream, bit_in),         # bit9  stream bit_out
          Z, Z, Z, Z, Z, Z]

    # --- 输出封存（协议：输出 = 最后 83 个信号） ---
    c.sealed_outputs = c.seal_outputs(out_word + st + [d0, d1, d2])
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


# keccak_link 轮常数（RC 注入窗 = 轮内拍 76，kl_split 版协议）
from .keccak_link import KECCAK_RC  # noqa: E402

MASK64 = (1 << 64) - 1
MASK256 = (1 << 256) - 1


class FairDiceDriver:
    """
    FairDice Core 标准化接口驱动器（全局 4 拍网格由 ticks%4 跟踪）。

    标准掷骰接口：
      auth_structural(r, s)  → unit=0 门级结构校验 + MUL 锚点，返回 fail
      hash_seed(r, s)        → unit=1 Keccak-256(r‖s)，返回 256 位种子
      mix_height(seed, h)    → unit=2 (seed + h) mod 2^256
      stream_dice(summed)    → unit=3 位流 + 壳内 mod-6，返回点数 1..6
      roll(z, r, s, v, h)    → 全流程，返回 (dice, seed, fail)
    """

    def __init__(self, sim, m_n: int):
        self.sim = sim
        self.m = m_n                       # AUTH 结构校验模数（secp256k1 n）
        self.rc = (1 << 256) - m_n
        self.ticks = 0
        self.shadow = [0] * 16             # AUTH 环影子（环 0..4 / 8..12）

    # ---------------- 底层 ----------------
    @property
    def ph(self) -> int:
        return self.ticks % 4

    def _beat(self, bus_word: int, unit: int, ctrl: int) -> List[bool]:
        out = self.sim.beat(pack_input(bus_word, unit, ctrl))
        self.ticks += 1
        return out

    def idle(self, n: int = 1):
        """全单元安全空闲拍（AUTH/HASH 门控归零，MIX 强制 READ，STREAM 自恢复）"""
        for _ in range(n):
            self._beat(0, UNIT_HASH, 0)

    def _align_ph0(self):
        while self.ticks % 4 != 0:
            self.idle()

    # ================= unit=0：AUTH（ecrecover_ctrl，端口映射自 CtrlDriver） ====
    def _au_tick(self, word=0, cmd=0, start=False):
        ctrl = cmd | (0x8 if start else 0)
        return self._beat(word, UNIT_AUTH, ctrl)

    def _to_ph3(self):
        while self.ph != 3:
            self.idle()

    def _au_accept(self, cmd, aux):
        self._to_ph3()
        self._au_tick(word=aux, cmd=cmd, start=True)

    def _au_frame(self, words=None):
        assert self.ph == 0, f"AUTH 帧未对齐 ph0（ph={self.ph}）"
        for w in range(4):
            self._au_tick(word=0 if words is None else words[w])

    def _au_fetch(self, src, pin_val=None):
        self._au_frame(words_of(pin_val) if src == PIN else None)

    def _au_macc(self):
        self._to_ph3()
        self._au_tick()

    def _au_wait_m_done(self):
        while True:
            out = self._au_tick()
            if out[69]:
                return

    def _au_wait_mcore_idle(self):
        while True:
            out = self._au_tick()
            if not out[68] and not out[69]:
                break
        self._au_tick()

    def _au_finish(self):
        while True:
            out = self._au_tick()
            if out[65]:                  # status[1] = auth done
                break
        for _ in range(3):
            self._au_tick()
        assert self.ph == 0

    def au_fail(self) -> bool:
        out = self._au_tick()
        return bool(out[66])             # status[2] = auth fail（粘滞）

    def write_ring(self, dst, value):
        assert dst <= 4 or 8 <= dst <= 12
        self._au_accept(CMD_WRITE, dst << 12)
        self._au_frame(words_of(value))
        self._au_finish()
        self.shadow[dst] = value & MASK256

    def read_ring(self, src):
        assert src <= 4 or 8 <= src <= 12
        self._au_accept(CMD_READ, src << 12)
        ws = []
        for _ in range(4):
            out = self._au_tick()
            ws.append(bits_to_word(out[:64]))
        self._au_finish()
        return sum(w << (64 * i) for i, w in enumerate(ws))

    def exec_mul(self, sa, sb, dst):
        """MUL dst = sa·sb mod m（mcore 全流程；操作数限环驻）"""
        assert sa != PIN and sb != PIN
        a, b = self.shadow[sa], self.shadow[sb]
        self._au_accept(CMD_EXEC, OP_MUL | (sa << 4) | (sb << 8) | (dst << 12))
        self._au_macc()                       # M1 LD_C 接受
        self._au_frame(words_of(self.rc))     # M2 喂 rc
        self._au_macc()                       # M3 LD_A 接受
        self._au_fetch(sa)                    # M4 流 A
        self._au_fetch(sb)                    # M5 piso 装 B（末拍同拍 LD_B）
        for _ in range(256):
            self._au_tick()                   # M6 位流
        self._au_macc()                       # M7 RUN 接受
        self._au_wait_m_done()                # M8
        self._au_wait_mcore_idle()
        self._au_macc()                       # M9 LD_C 接受
        self._au_frame(words_of(self.m))      # M10 喂 m
        self._au_macc()                       # M11 REDUCE 接受
        self._au_wait_m_done()                # M12
        self._au_wait_mcore_idle()
        self._au_macc()                       # M13 READ 接受
        self._au_frame()                      # M14 捕获
        self._au_frame()                      # WB 写回
        if dst != NULL:
            self.shadow[dst] = (a * b) % self.m
        self._au_finish()

    def exec_ne0(self, sa):
        """NE0：srcA == 0 → fail 置位"""
        self._au_accept(CMD_EXEC, OP_NE0 | (sa << 4) | (NULL << 8) | (NULL << 12))
        self._au_fetch(sa)
        self._au_finish()

    def exec_ltchk(self, sa, pin_val):
        """LTCHK：srcA >= pin_val（模数 n）→ fail 置位（srcB=PIN 引脚直供）"""
        self._au_accept(CMD_EXEC, OP_LTCHK | (sa << 4) | (PIN << 8) | (NULL << 12))
        self._au_fetch(sa)                    # LC1 LOAD srcA
        self._au_fetch(PIN, pin_val)          # LC2 SUB srcB（PIN 常数流）
        self._au_finish()

    def auth_structural(self, r: int, s: int, anchor: bool = True) -> bool:
        """
        门级签名结构校验：1 ≤ r,s < n，可选 MUL 锚点（T2 = r·s mod n 回读对拍）。
        返回 fail（False = 结构有效）。
        """
        X, Y, T2 = 0, 1, 10
        self.write_ring(X, r)
        self.write_ring(Y, s)
        self.exec_ne0(X)                   # r == 0 → fail
        self.exec_ne0(Y)                   # s == 0 → fail
        self.exec_ltchk(X, self.m)         # r >= n → fail
        self.exec_ltchk(Y, self.m)         # s >= n → fail
        if anchor:
            self.exec_mul(X, Y, T2)        # 模乘锚点（mcore/piso/fadd 全通路）
            assert self.read_ring(T2) == (r * s) % self.m, "MUL 锚点不符"
        return self.au_fail()

    # ================= unit=1：HASH（kl_top，协议同 test_kl_split） ============
    def hash_seed(self, msg64: bytes) -> int:
        """Keccak-256(msg64)（64 字节 = r‖s），返回 256 位整数种子"""
        assert len(msg64) == 64
        lanes = [int.from_bytes(msg64[8 * i:8 * i + 8], "little")
                 for i in range(8)]

        def absorb_word(t):
            if t < 8:
                return lanes[t]
            if t == 8:
                return 0x01
            if t == 16:
                return 0x8000000000000000
            return 0

        self._beat(0, UNIT_HASH, 0x1)              # beat 0: start
        for t in range(25):                        # ABSORB 25 拍
            self._beat(absorb_word(t), UNIT_HASH, 0)
        for rnd in range(24):                      # 24 轮 × 100 拍
            for rt in range(100):
                w = KECCAK_RC[rnd] if rt == 76 else 0
                self._beat(w, UNIT_HASH, 0)
        digest = []
        for i in range(5):                         # SQUEEZE 5 拍
            out = self._beat(0, UNIT_HASH, 0)
            if i >= 1:                             # stick 1..4 = 车道 0..3
                digest.append(bits_to_word(out[:64]))
        out = self._beat(0, UNIT_HASH, 0)
        assert out[71], "kl_top done 未置位"        # status[7] = hash done
        digest = b"".join(w.to_bytes(8, "little") for w in digest)
        return int.from_bytes(digest, "big")

    # ================= unit=2：MIX（fadd64b） =================================
    def _mix_op(self, op: int, x: int) -> List[bool]:
        out = None
        for i, w in enumerate(words_of(x)):
            ctrl = op | ((1 if i == 0 else 0) << 2)
            out = self._beat(w, UNIT_MIX, ctrl)
        return out

    def mix_height_words(self, seed: int, height: int) -> int:
        """sum = (seed + height) mod 2^256（fadd64 LOAD→ADD→READ，进位丢弃）"""
        self._mix_op(FA_LOAD, seed)
        self._mix_op(FA_ADD, height)
        ws = []
        for i in range(4):
            ctrl = FA_READ | ((1 if i == 0 else 0) << 2)
            out = self._beat(0, UNIT_MIX, ctrl)
            ws.append(bits_to_word(out[:64]))
        return sum(w << (64 * i) for i, w in enumerate(ws))

    # ================= unit=3：STREAM（piso256 + 壳内 mod-6 累加器） ==========
    def stream_dice(self, value: int) -> int:
        """装入 256 位并逐位流出喂壳内累加器，返回点数 1..6（dice = acc+1）"""
        self._align_ph0()                          # 装入窗口 ph0 对齐
        ws = words_of(value)
        # 首拍：load_en + acc_clear 同拍（清零累加器并装入 word0）
        self._beat(ws[0], UNIT_STREAM, 0x1 | 0x4)
        for w in ws[1:]:
            self._beat(w, UNIT_STREAM, 0x1)        # load_en，LSW 先行
        for _ in range(256):                       # stream_en 256 拍，MSB 先行
            self._beat(0, UNIT_STREAM, 0x2)
        out = self._beat(0, UNIT_STREAM, 0)        # 读 dice（acc 保持）
        dice = sum(int(out[80 + k]) << k for k in range(3))
        return dice

    # ================= 全流程 ================================================
    def roll(self, r: int, s: int, height: int, anchor: bool = True):
        """掷骰全流程：AUTH → HASH → MIX → STREAM，返回 (dice, seed, fail)"""
        fail = self.auth_structural(r, s, anchor=anchor)
        msg = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        seed = self.hash_seed(msg)
        summed = self.mix_height_words(seed, height)
        dice = self.stream_dice(summed)
        return dice, seed, fail
