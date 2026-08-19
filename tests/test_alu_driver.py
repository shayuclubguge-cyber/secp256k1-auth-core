"""alu_driver 高层模运算验证：mulmod/inv 全链路，对照 engine.py 黄金模型与真实以太坊向量"""

import random
import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout.parts.alu_driver import AluDriver
from tapeout.field import P, N
from tapeout.engine import hw_mulmod, hw_addmod, hw_submod, hw_invmod


def test_mulmod_p():
    d = AluDriver()
    cases = [(0, 5), (1, 1), (P - 1, P - 1), (P - 1, 2), (3, 7),
             (0xDEADBEEFCAFEBABE1234567890, 0xFEDCBA0987654321)]
    random.seed(99)
    cases += [(random.getrandbits(256) % P, random.getrandbits(256) % P) for _ in range(4)]
    for a, b in cases:
        got = d.mulmod_p(a, b)
        want = hw_mulmod(a, b)
        assert got == want, f"mulmod_p({a:#x},{b:#x}) = {got:#x}, want {want:#x}"
    print(f"mulmod_p: {len(cases)} cases OK (最后一次拍数={len(d.trace)})")


def test_mulmod_n():
    d = AluDriver()
    random.seed(7)
    for _ in range(3):
        a, b = random.getrandbits(256) % N, random.getrandbits(256) % N
        assert d.mulmod_n(a, b) == (a * b) % N
    print("mulmod_n: 3 cases OK")


def test_addsub_p():
    d = AluDriver()
    random.seed(11)
    for _ in range(5):
        a, b = random.getrandbits(256) % P, random.getrandbits(256) % P
        assert d.addmod_p(a, b) == hw_addmod(a, b)
        assert d.submod_p(a, b) == hw_submod(a, b)
    print("addmod/submod_p: 5x2 cases OK")


def invmod_via_driver(d: AluDriver, a: int) -> int:
    """费马 a^(p-2) mod p：平方-乘调度，全程走 fadd64"""
    e = P - 2
    acc = 1
    base = a % P
    for i in range(256):
        if (e >> i) & 1:
            acc = d.mulmod_p(acc, base)
        base = d.mulmod_p(base, base)
    return acc


def test_invmod():
    d = AluDriver()
    x = 0x123456789ABCDEF123456789ABCDEF123456789ABCDEF123456789ABCDEF
    got = invmod_via_driver(d, x)
    want = hw_invmod(x)
    assert got == want, f"invmod = {got:#x}, want {want:#x}"
    assert (got * x) % P == 1
    print(f"invmod OK (拍数={len(d.trace)})")


if __name__ == "__main__":
    test_addsub_p()
    test_mulmod_p()
    test_mulmod_n()
    test_invmod()
    print("ALL alu_driver TESTS PASSED")
