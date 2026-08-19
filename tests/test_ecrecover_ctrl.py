"""
ecrecover_ctrl 主电路拍级验证（L1：单宏指令全路径对拍）

组装五颗已上链零件（全真实网表，非函数式替身）：
  cid1 fadd64（mcore 内部）/ cid2 mcore256 / cid3 piso256
  cid4 ringbusA / cid5 ringbusB / cid6 fadd64b

覆盖：
  结构：指令数 / 字节数 / 引脚数（上链约束 ≤24,575B）
  WRITE_RING / READ_RING 回读（双环）
  MOV（环内 + 跨环）
  ADD 三条路径（a+b<p / p≤a+b<2^256 / a+b≥2^256 溢出补偿）
  SUB 两条路径（不借位 / 借位 +m 还原）
  EQ0 / NE0 / LTCHK（fail 置位语义，粘滞）
  MUL（mcore 全流程：LD_C→LD_A→LD_B(piso)→RUN→LD_C→REDUCE→READ→WB）

驱动器 = ctrl FSM 的影子状态机：所有窗口按 ctrl 状态迁移的确定性推算，
m_done 由输出 pin 69 观测，分支路径由影子黄金值预测（与链上驱动器同构）。
"""

import random
import sys

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout.field import P, N
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.fadd64 import build_fadd64
from tapeout.parts.mcore256 import build_mcore256
from tapeout.parts.piso256 import build_piso256
from tapeout.parts.ringbus import build_ringbus5
from tapeout.parts.ecrecover_ctrl import (
    build_ecrecover_ctrl, OP_NOP, OP_MOV, OP_EQ0, OP_NE0, OP_LTCHK,
    OP_ADD, OP_SUB, OP_MUL, PIN, NULL,
)

V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
MASK64 = (1 << 64) - 1
MASK = (1 << 256) - 1

# 环映射（bit3=环选择：0..4=ringbusA，8..12=ringbusB）
X, Y, Z, T1, R0 = 0, 1, 2, 3, 4
BX, BY, T2, T3, T4 = 8, 9, 10, 11, 12


def words_of(x: int):
    return [(x >> (64 * i)) & MASK64 for i in range(4)]


def bits_to_word64(bits) -> int:
    return sum(int(b) << k for k, b in enumerate(bits))


def build_sim():
    resolver = REFResolver()
    resolver.register(V2_CPU, 1, build_fadd64())            # mcore 内部 REF
    resolver.register(V2_CPU, 2, build_mcore256(V2_CPU, 1))
    resolver.register(V2_CPU, 3, build_piso256())
    resolver.register(V2_CPU, 4, build_ringbus5())
    resolver.register(V2_CPU, 5, build_ringbus5())
    resolver.register(V2_CPU, 6, build_fadd64())
    c = build_ecrecover_ctrl(V2_CPU)
    return c, SimWithREF(c, resolver)


class CtrlDriver:
    """ecrecover_ctrl 拍级驱动器（ph 本地跟踪；模数 m 由驱动器供常数）"""

    def __init__(self, sim, m=P):
        self.sim = sim
        self.m = m
        self.rc = (1 << 256) - m
        self.ph = 0
        self.ticks = 0
        self.shadow = [0] * 16
        self.fail_expected = False

    # ---------------- 基础节拍 ----------------
    def _tick(self, word=0, cmd=0, start=False):
        ins = [bool((word >> k) & 1) for k in range(64)]
        ins += [bool(cmd & 1), bool(cmd & 2), bool(cmd & 4), bool(start)]
        out = self.sim.beat(ins)
        self.ph = (self.ph + 1) % 4
        self.ticks += 1
        return out

    def _to_ph3(self):
        while self.ph != 3:
            self._tick()

    def _accept(self, cmd, aux):
        assert self.ph == 3 or True
        self._to_ph3()
        self._tick(word=aux, cmd=cmd, start=True)

    def _frame(self, words=None):
        """4 拍帧（进入时必为 ph0）；words 给则逐拍喂字"""
        assert self.ph == 0, f"帧未对齐 ph0（ph={self.ph}）"
        for w in range(4):
            self._tick(word=0 if words is None else words[w])

    def _fetch(self, src, pin_val=None):
        """取数帧：环操作数空转；PIN 操作数喂常数"""
        self._frame(words_of(pin_val) if src == PIN else None)

    def _macc(self):
        """mcore 命令接受态：空转到 ph3 拍（该拍 g_acc 起火）"""
        self._to_ph3()
        self._tick()

    def _wait_m_done(self):
        while True:
            out = self._tick()
            if out[69]:
                return

    def _wait_mcore_idle(self):
        """m_done 4 拍脉冲过完，再空 1 拍让 ctrl 的 m_idle_r 置位"""
        while True:
            out = self._tick()
            if not out[68] and not out[69]:
                break
        self._tick()

    def _finish(self):
        """等 done 脉冲（DONE 态 4 拍），过完落在 IDLE ph0"""
        while True:
            out = self._tick()
            if out[65]:
                break
        for _ in range(3):
            self._tick()
        assert self.ph == 0

    def check_fail(self):
        out = self._tick()
        assert bool(out[66]) == self.fail_expected, \
            f"fail={bool(out[66])} 期望 {self.fail_expected}"

    # ---------------- 顶层命令 ----------------
    def write_ring(self, dst, value):
        assert dst <= 4 or 8 <= dst <= 12
        self._accept(1, dst << 12)
        self._frame(words_of(value))
        self._finish()
        self.shadow[dst] = value & MASK

    def read_ring(self, src):
        assert src <= 4 or 8 <= src <= 12
        self._accept(3, src << 12)
        ws = []
        for _ in range(4):
            out = self._tick()
            ws.append(bits_to_word64(out[:64]))
        self._finish()
        return sum(w << (64 * i) for i, w in enumerate(ws))

    # ---------------- EXEC 宏指令 ----------------
    def exec(self, op, sa, sb=NULL, dst=NULL, pin_a=None, pin_b=None):
        macro = op | (sa << 4) | (sb << 8) | (dst << 12)
        self._accept(2, macro)
        a = self.shadow[sa] if sa != PIN else (pin_a or 0)
        b = self.shadow[sb] if sb != PIN else (pin_b or 0)

        if op == OP_NOP:
            pass
        elif op == OP_MOV:
            assert sa != PIN
            self._fetch(sa)                    # MV 捕获
            self._frame()                      # WB 写回
            if dst != NULL:
                self.shadow[dst] = a
        elif op in (OP_EQ0, OP_NE0):
            assert sa != PIN
            self._fetch(sa)                    # EQ：4 拍 OR 归约
            if op == OP_EQ0:
                self.fail_expected |= (a != 0)
            else:
                self.fail_expected |= (a == 0)
        elif op == OP_LTCHK:
            self._fetch(sa, pin_a)             # LC1 LOAD srcA
            self._fetch(sb, pin_b)             # LC2 SUB srcB
            self.fail_expected |= (a >= b)
        elif op == OP_ADD:
            self._fetch(sa, pin_a)             # A1 LOAD a
            self._fetch(sb, pin_b)             # A2 ADD b
            s = a + b
            if s >= (1 << 256):
                self._frame(words_of(self.rc))   # A3 +rc 补偿
                self._frame(words_of(self.m))    # A4 SUB m（必借位）
                self._frame(words_of(self.m))    # A5 +m 还原
            else:
                self._frame(words_of(self.m))    # A4 SUB m
                if s < self.m:
                    self._frame(words_of(self.m))  # A5 借位还原
            self._frame()                      # WB
            if dst != NULL:
                self.shadow[dst] = s % self.m
        elif op == OP_SUB:
            self._fetch(sa, pin_a)             # S1 LOAD a
            self._fetch(sb, pin_b)             # S2 SUB b
            if a < b:
                self._frame(words_of(self.m))  # S3 +m 还原
            self._frame()                      # WB
            if dst != NULL:
                self.shadow[dst] = (a - b) % self.m
        elif op == OP_MUL:
            self._macc()                       # M1 LD_C 接受
            self._frame(words_of(self.rc))     # M2 喂 rc
            self._macc()                       # M3 LD_A 接受
            self._fetch(sa, pin_a)             # M4 流 A
            self._fetch(sb, pin_b)             # M5 piso 装 B（末拍同拍 LD_B）
            for _ in range(256):
                self._tick()                   # M6 位流
            self._macc()                       # M7 RUN 接受
            self._wait_m_done()                # M8
            self._wait_mcore_idle()
            self._macc()                       # M9 LD_C 接受
            self._frame(words_of(self.m))      # M10 喂 m
            self._macc()                       # M11 REDUCE 接受
            self._wait_m_done()                # M12
            self._wait_mcore_idle()
            self._macc()                       # M13 READ 接受
            self._frame()                      # M14 捕获 mcore.out
            self._frame()                      # WB 写回
            if dst != NULL:
                self.shadow[dst] = (a * b) % self.m
        else:
            raise ValueError(op)

        self._finish()                         # DONE 4 拍


def main():
    c, sim = build_sim()
    blob = c.to_bytes()
    n_inst = len(c.instructions)
    print(f"ecrecover_ctrl: {c.gate_count()} NAND, {n_inst} 指令, "
          f"{len(blob)}B, 引脚 {c.n_in}/{c.n_out}")
    assert len(blob) <= 24575, f"超 OKX 24,575B 上限: {len(blob)}B"
    assert c.n_in == 68 and c.n_out == 70

    d = CtrlDriver(sim, m=P)

    # --- WRITE/READ 双环回读 ---
    vx = 0xC0FFEE_12345678_DEADBEEF_99887766_A5A5A5A5_5A5A5A5A_F00DF00D_0BAD0BAD
    vy = P - 1
    d.write_ring(X, vx)
    d.write_ring(BX, vy)                       # ringB 侧
    assert d.read_ring(X) == vx, "ringA 回读失败"
    assert d.read_ring(BX) == vy, "ringB 回读失败"
    d.check_fail()
    print(f"✓ WRITE_RING/READ_RING 双环回读（{d.ticks} 拍）")

    # --- MOV 环内 + 跨环 ---
    d.exec(OP_MOV, X, NULL, T1)                # ringA 内
    assert d.read_ring(T1) == vx
    d.exec(OP_MOV, BX, NULL, T3)               # ringB 内
    assert d.read_ring(T3) == vy
    d.exec(OP_MOV, T1, NULL, T4)               # ringA → ringB
    assert d.read_ring(T4) == vx
    d.check_fail()
    print(f"✓ MOV 环内/跨环（{d.ticks} 拍）")

    # --- ADD 三路径 ---
    va, vb = 0x12345, 0x54321                  # 路径1: a+b < p
    d.write_ring(X, va)
    d.write_ring(Y, vb)
    d.exec(OP_ADD, X, Y, T1)
    assert d.read_ring(T1) == va + vb
    print(f"✓ ADD a+b<p（{d.ticks} 拍）")

    d.write_ring(X, P - 5)                     # 路径2: p ≤ a+b < 2^256
    d.write_ring(Y, 10)
    d.exec(OP_ADD, X, Y, T1)
    assert d.read_ring(T1) == 5, f"got {d.shadow[T1]:#x}"
    print(f"✓ ADD p≤a+b<2^256（{d.ticks} 拍）")

    d.write_ring(X, P - 1)                     # 路径3: a+b ≥ 2^256（溢出补偿）
    d.write_ring(Y, P - 1)
    d.exec(OP_ADD, X, Y, T1)
    assert d.read_ring(T1) == (2 * P - 2) % P
    print(f"✓ ADD a+b≥2^256 溢出补偿（{d.ticks} 拍）")

    # --- PIN 直供操作数 ---
    d.write_ring(X, 100)
    d.exec(OP_ADD, X, PIN, T1, pin_b=P - 50)   # 100 + (p-50) = 50
    assert d.read_ring(T1) == 50
    print(f"✓ ADD srcB=PIN 引脚直供（{d.ticks} 拍）")

    # --- SUB 两路径 ---
    d.write_ring(X, 777)
    d.write_ring(Y, 77)
    d.exec(OP_SUB, X, Y, T1)                   # 不借位
    assert d.read_ring(T1) == 700
    d.exec(OP_SUB, Y, X, T2)                   # 借位 → +p 还原
    assert d.read_ring(T2) == (77 - 777) % P
    print(f"✓ SUB 借位/不借位（{d.ticks} 拍）")

    # --- EQ0 / NE0 / LTCHK（fail 语义；最后做，fail 粘滞） ---
    d.write_ring(Z, 0)
    d.exec(OP_EQ0, Z)                          # 0 == 0 → 不 fail
    d.check_fail()
    d.exec(OP_NE0, X)                          # X≠0 → 不 fail
    d.check_fail()
    d.write_ring(X, 5)
    d.write_ring(Y, 9)
    d.exec(OP_LTCHK, X, Y)                     # 5 < 9 → 不 fail
    d.check_fail()
    print(f"✓ EQ0/NE0/LTCHK 通过路径 fail=0（{d.ticks} 拍）")

    d.exec(OP_LTCHK, Y, X)                     # 9 ≥ 5 → fail 置位
    d.check_fail()
    print(f"✓ LTCHK 越界 fail 置位（{d.ticks} 拍）")

    # --- MUL（mcore 全流程，全新 sim 以免 fail 粘滞干扰检查） ---
    c2, sim2 = build_sim()
    d2 = CtrlDriver(sim2, m=P)
    cases = [
        (3, 5, "3·5"),
        (P - 1, P - 1, "(p-1)²"),
    ]
    rng = random.Random(7)
    cases += [(rng.getrandbits(256) % P, rng.getrandbits(256) % P, f"fuzz#{i}")
              for i in range(2)]
    for va, vb, tag in cases:
        t0 = d2.ticks
        d2.write_ring(X, va)
        d2.write_ring(Y, vb)
        d2.exec(OP_MUL, X, Y, T1)
        got = d2.read_ring(T1)
        want = (va * vb) % P
        assert got == want, f"MUL {tag}: got={got:#x} want={want:#x}"
        print(f"✓ MUL {tag}: {d2.ticks - t0} 拍")

    # MUL srcB=PIN（常数乘法走引脚）
    d2.write_ring(X, 0xABCD)
    d2.exec(OP_MUL, X, PIN, T2, pin_b=0x1234)
    assert d2.read_ring(T2) == (0xABCD * 0x1234) % P
    print(f"✓ MUL srcB=PIN（{d2.ticks} 拍）")
    d2.check_fail()

    # MUL mod n（模数仅由驱动常数决定）
    c3, sim3 = build_sim()
    d3 = CtrlDriver(sim3, m=N)
    va, vb = 0xDEADBEEF_C0FFEE, 0x1234567890ABCDEF
    d3.write_ring(X, va)
    d3.write_ring(Y, vb)
    d3.exec(OP_MUL, X, Y, T1)
    assert d3.read_ring(T1) == (va * vb) % N
    print(f"✓ MUL mod n（{d3.ticks} 拍）")

    print(f"\necrecover_ctrl L1 全部通过：{n_inst} 指令 / {len(blob)}B / "
          f"单 MUL 全树仿真 OK")


if __name__ == "__main__":
    main()
