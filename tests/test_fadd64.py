"""fadd64 字串行 ALU 本地验证"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout.parts.fadd64 import (
    build_fadd64, make_input, words_of, bits_to_word,
    OP_LOAD, OP_ADD, OP_SUB, OP_READ,
)
from tapeout.simulator import Simulator
from tapeout.field import P, N

MASK = (1 << 256) - 1
R_P = (1 << 256) - P   # 2^32 + 977
R_N = (1 << 256) - N


class Fadd64Driver:
    """驱动器：与链下 beat 驱动逻辑一致"""

    def __init__(self, sim: Simulator):
        self.sim = sim

    def _beat(self, word, op, start):
        out = self.sim.beat(make_input(word, op, start))
        return bits_to_word(out[:64]), bool(out[64])

    def op(self, op, x):
        """4 拍流一个字；cout 在算术第 4 拍直通输出（组合旁路），无需补拍"""
        outs, cout = [], False
        for i, w in enumerate(words_of(x)):
            ow, c = self._beat(w, op, i == 0)
            outs.append(ow)
            cout = c
        return outs, cout

    def load(self, a):
        self.op(OP_LOAD, a)

    def read(self):
        outs, _ = self.op(OP_READ, 0)
        return sum(w << (64 * i) for i, w in enumerate(outs))

    def add(self, b):
        _, cout = self.op(OP_ADD, b)
        return cout

    def sub(self, b):
        _, cout = self.op(OP_SUB, b)
        return not cout   # 借位 = NOT carry-out（a + ~b + 1 的进位取反）


def test_raw_add_sub():
    c = build_fadd64()
    sim = Simulator(c)
    d = Fadd64Driver(sim)

    cases = [
        (0, 0), (1, 1), (MASK, 1), (MASK, MASK),
        (0xDEADBEEFCAFEBABE, 0x1234567890ABCDEF),
        (P - 1, P - 1), (1 << 255, 1 << 255),
    ]
    for a, b in cases:
        d.load(a)
        cout = d.add(b)
        got = d.read()
        want = (a + b) & MASK
        assert got == want, f"ADD({a:#x},{b:#x}) = {got:#x}, want {want:#x}"
        assert cout == ((a + b) >> 256 & 1), f"cout wrong for {a:#x}+{b:#x}"

        d.load(a)
        bout = d.sub(b)
        got = d.read()
        want = (a - b) & MASK
        assert got == want, f"SUB({a:#x},{b:#x}) = {got:#x}, want {want:#x}"
        assert bout == (1 if a < b else 0), f"bout wrong for {a:#x}-{b:#x}"
    print(f"raw add/sub: {len(cases)}x2 cases OK  (gates={c.gate_count()}, insts={len(c.instructions)})")


def addmod_via_alu(d: Fadd64Driver, a: int, b: int, m: int, r: int) -> int:
    """用 fadd64 编排完整 mod 加法（与链上驱动序列一致）"""
    d.load(a)
    cout = d.add(b)
    if cout:
        d.add(r)
    bout = d.sub(m)
    if bout:
        d.add(m)
    return d.read()


def submod_via_alu(d: Fadd64Driver, a: int, b: int, m: int) -> int:
    d.load(a)
    bout = d.sub(b)
    if bout:
        d.add(m)
    return d.read()


def test_mod_p():
    c = build_fadd64()
    sim = Simulator(c)
    d = Fadd64Driver(sim)
    cases = [
        (0, 0), (1, 2), (P - 1, 1), (P - 1, P - 1), (P - 2, P - 2),
        (1 << 255, (1 << 255) - 1), (3, P - 5), (MASK % P, MASK % P),
    ]
    for a, b in cases:
        got = addmod_via_alu(d, a, b, P, R_P)
        want = (a + b) % P
        assert got == want, f"addmod_p({a:#x},{b:#x}) = {got:#x}, want {want:#x}"
        got = submod_via_alu(d, a, b, P)
        want = (a - b) % P
        assert got == want, f"submod_p({a:#x},{b:#x}) = {got:#x}, want {want:#x}"
    print(f"mod-p add/sub: {len(cases)}x2 cases OK")


def test_mod_n():
    c = build_fadd64()
    sim = Simulator(c)
    d = Fadd64Driver(sim)
    cases = [(1, 2), (N - 1, 2), (N - 1, N - 1), (N // 2, N // 2 + 3)]
    for a, b in cases:
        got = addmod_via_alu(d, a, b, N, R_N)
        assert got == (a + b) % N, f"addmod_n({a:#x},{b:#x})"
        got = submod_via_alu(d, a, b, N)
        assert got == (a - b) % N, f"submod_n({a:#x},{b:#x})"
    print(f"mod-n add/sub: {len(cases)}x2 cases OK")


def test_consecutive_ops():
    """连续多次运算（不重新 LOAD 的中间形态）：ACC 复用"""
    c = build_fadd64()
    sim = Simulator(c)
    d = Fadd64Driver(sim)
    acc = 0
    d.load(0)
    import random
    random.seed(7)
    for _ in range(20):
        x = random.getrandbits(256)
        if random.random() < 0.5:
            d.add(x)
            acc = (acc + x) & MASK
        else:
            d.sub(x)
            acc = (acc - x) & MASK
        assert d.read() == acc, f"chained op mismatch at x={x:#x}"
    print("chained 20 ops OK")


def test_fuzz():
    """随机模糊测试：mod-p / mod-n / 裸加减混合"""
    import random
    random.seed(20260818)
    c = build_fadd64()
    sim = Simulator(c)
    d = Fadd64Driver(sim)
    for t in range(30):
        a, b = random.getrandbits(256), random.getrandbits(256)
        m, r = (P, R_P) if t % 2 == 0 else (N, R_N)
        a %= m
        b %= m
        got = addmod_via_alu(d, a, b, m, r)
        assert got == (a + b) % m, f"fuzz addmod {t}: a={a:#x} b={b:#x}"
        got = submod_via_alu(d, a, b, m)
        assert got == (a - b) % m, f"fuzz submod {t}"
    print("fuzz 30x2 (mod-p/mod-n) OK")


if __name__ == "__main__":
    test_raw_add_sub()
    test_mod_p()
    test_mod_n()
    test_consecutive_ops()
    test_fuzz()
    print("ALL fadd64 TESTS PASSED")
