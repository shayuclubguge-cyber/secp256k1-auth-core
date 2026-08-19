"""
集成冒烟：piso256 → mcore256 LD_B 位流全对齐对拍

pico_mul 母电路（纯组合胶水，零自有 LATCH）：
  REF piso256：in_word=bus, load_en=pload, stream_en=pstream
  REF mcore256：pin0 = mux(bsel, bus[0], piso.bit_out)，其余 = bus/mcmd/mstart

验证点：piso 的 256 拍位流窗口与 mcore LD_B 的 256 拍消费窗口逐拍对齐，
一次完整 mulmod（LD_C→LD_A→piso装→LD_B→RUN→LD_C→REDUCE→READ）结果正确。
这正是 ecrecover_ctrl 里每一次模乘的接线原型。
"""

import random
import sys

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout import Circuit
from tapeout.field import P, N
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.fadd64 import build_fadd64
from tapeout.parts.mcore256 import (
    build_mcore256, CMD_LD_A, CMD_LD_B, CMD_LD_C, CMD_RUN, CMD_READ, CMD_REDUCE,
)
from tapeout.parts.piso256 import build_piso256

V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
V2_FADD64_CID = 1
# 零件的「未来链上键」（本地仿真任意固定值即可，保持与将来流片一致的习惯）
V2_PISO_CID = 3      # 预定 v2 cid3
V2_MCORE_CID = 2     # 已有

MASK = (1 << 256) - 1


def build_pico_mul() -> Circuit:
    c = Circuit("pico_mul", n_in=71, n_out=66)
    pins = c.input_signals()
    bus = pins[0:64]
    mcmd = pins[64:67]
    mstart = pins[67]
    pload = pins[68]
    pstream = pins[69]
    bsel = pins[70]

    piso_out = c.ref(V2_CPU, V2_PISO_CID, 66, 1, bus + [pload, pstream])
    bit = piso_out[0]

    bsel_n = c.not_(bsel)
    pin0 = c.nand(c.nand(bus[0], bsel_n), c.nand(bit, bsel))

    mcore_in = [pin0] + bus[1:64] + mcmd + [mstart]
    mout = c.ref(V2_CPU, V2_MCORE_CID, 68, 66, mcore_in)
    c.seal_outputs(mout)
    return c


class PicoDriver:
    """母电路驱动：与 MCoreDriver 同网格纪律（ph 本地跟踪，ph3 发令）"""

    def __init__(self, sim):
        self.sim = sim
        self.ph = 0
        self.ticks = 0

    def _tick(self, word=0, mcmd=0, mstart=False, pload=False,
              pstream=False, bsel=False):
        ins = [bool((word >> k) & 1) for k in range(64)]
        ins += [bool(mcmd & 1), bool(mcmd & 2), bool(mcmd & 4),
                mstart, pload, pstream, bsel]
        out = self.sim.beat(ins)
        self.ph = (self.ph + 1) % 4
        self.ticks += 1
        return out

    def _to_ph3(self):
        while self.ph != 3:
            self._tick()

    def _mopen(self, cmd):
        self._to_ph3()
        self._tick(mcmd=cmd, mstart=True)

    def load_c(self, v):
        self._mopen(CMD_LD_C)
        for w in range(4):
            self._tick(word=(v >> (64 * w)) & MASK & ((1 << 64) - 1), mcmd=CMD_LD_C)

    def load_a(self, a):
        self._mopen(CMD_LD_A)
        for w in range(4):
            self._tick(word=(a >> (64 * w)) & ((1 << 64) - 1), mcmd=CMD_LD_A)

    def load_b_via_piso(self, b):
        # 网格纪律：piso 装入窗口 ph0..3，第 4 拍（ph3）同拍发 LD_B 请求
        # → 装入结束与流出开始间隔 = 0（≡0 mod 4），tap 对齐
        while self.ph != 0:
            self._tick()
        for w in range(3):
            self._tick(word=(b >> (64 * w)) & ((1 << 64) - 1), pload=True)
        self._tick(word=(b >> 192) & ((1 << 64) - 1), pload=True,
                   mcmd=CMD_LD_B, mstart=True)
        for _ in range(256):
            self._tick(mcmd=CMD_LD_B, pstream=True, bsel=True)

    def run(self):
        self._mopen(CMD_RUN)
        while True:
            out = self._tick()
            if out[65]:
                for _ in range(3):
                    self._tick()
                return

    def reduce(self):
        self._mopen(CMD_REDUCE)
        while True:
            out = self._tick()
            if out[65]:
                for _ in range(3):
                    self._tick()
                return

    def read(self):
        self._mopen(CMD_READ)
        ws = []
        for _ in range(4):
            out = self._tick(mcmd=CMD_READ)
            ws.append(sum(int(b) << k for k, b in enumerate(out[:64])))
        return sum(w << (64 * i) for i, w in enumerate(ws))

    def mulmod(self, a, b, m):
        a %= m
        rc = (1 << 256) - m
        self.load_c(rc)
        self.load_a(a)
        self.load_b_via_piso(b)
        self.run()
        self.load_c(m)
        self.reduce()
        return self.read()


def main():
    resolver = REFResolver()
    resolver.register(V2_CPU, V2_FADD64_CID, build_fadd64())
    resolver.register(V2_CPU, V2_PISO_CID, build_piso256())
    resolver.register(V2_CPU, V2_MCORE_CID, build_mcore256(V2_CPU, V2_FADD64_CID))

    c = build_pico_mul()
    blob = c.to_bytes()
    print(f"pico_mul: {c.gate_count()} NAND + 2 REF, {len(blob)}B, "
          f"状态穿透={256 + 1041 + 257} 位（piso + mcore + fadd64）")

    sim = SimWithREF(c, resolver)
    d = PicoDriver(sim)

    cases = [
        (3, 5, P, "3·5 mod p"),
        (P - 1, P - 1, P, "(p-1)² mod p"),
        (0xC0FFEE, 0xDEADBEEF, N, "小常数 mod n"),
    ]
    rng = random.Random(99)
    cases += [(rng.getrandbits(256), rng.getrandbits(256), P, f"fuzzP#{i}")
              for i in range(2)]
    cases += [(rng.getrandbits(256), rng.getrandbits(256), N, f"fuzzN#{i}")
              for i in range(2)]

    for a, b, m, tag in cases:
        t0 = d.ticks
        got = d.mulmod(a, b, m)
        want = (a * b) % m
        assert got == want, f"{tag}: got={got:#x} want={want:#x}"
        print(f"✓ {tag}: {d.ticks - t0} 拍, 结果正确")

    print("\npiso256→mcore256 集成冒烟全部通过：LD_B 位流逐拍对齐成立")


if __name__ == "__main__":
    main()
