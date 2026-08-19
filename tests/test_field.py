"""
域运算门级测试：add_mod / sub_mod（mod p 与 mod n）
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

import random

from tapeout import Circuit
from tapeout.simulator import Simulator
from tapeout.field import P, N, add_mod, sub_mod, int_to_bits, bits_to_int


def build_op(op: str, modulus: int):
    c = Circuit(f"u256_{op}_mod", n_in=512, n_out=256)
    pins = c.input_signals()
    a, b = pins[:256], pins[256:]
    if op == "add":
        out = add_mod(c, a, b, modulus)
    else:
        out = sub_mod(c, a, b, modulus)
    out = [c.buf(s) for s in out]
    return c, out


def run_cases(op, modulus, cases):
    c, out = build_op(op, modulus)
    sim = Simulator(c)
    sim.set_outputs([s.id for s in out])
    print(f"  {c}")
    for a, b in cases:
        ref = (a + b) % modulus if op == "add" else (a - b) % modulus
        res = bits_to_int(sim.eval(int_to_bits(a) + int_to_bits(b)))
        assert res == ref, f"{op}: a={a}, b={b}, got={res}, want={ref}"


def edge_cases(m):
    return [
        (0, 0), (1, 1), (m - 1, 1), (m - 1, m - 1), (0, m - 1),
        (m // 2, m // 2), (1 << 255, 1 << 255),
        (m - 2, m - 2), (12345, 67890),
    ]


def test_add_mod_p():
    random.seed(1)
    cases = edge_cases(P) + [(random.randrange(P), random.randrange(P)) for _ in range(8)]
    run_cases("add", P, cases)
    print("✓ add_mod mod p 通过（边界 9 + 随机 8）")


def test_sub_mod_p():
    random.seed(2)
    cases = edge_cases(P) + [(random.randrange(P), random.randrange(P)) for _ in range(8)]
    run_cases("sub", P, cases)
    print("✓ sub_mod mod p 通过（边界 9 + 随机 8）")


def test_add_mod_n():
    random.seed(3)
    cases = edge_cases(N) + [(random.randrange(N), random.randrange(N)) for _ in range(8)]
    run_cases("add", N, cases)
    print("✓ add_mod mod n 通过（标量域，边界 9 + 随机 8）")


def test_sub_mod_n():
    random.seed(4)
    cases = edge_cases(N) + [(random.randrange(N), random.randrange(N)) for _ in range(8)]
    run_cases("sub", N, cases)
    print("✓ sub_mod mod n 通过（标量域，边界 9 + 随机 8）")


if __name__ == "__main__":
    test_add_mod_p()
    test_sub_mod_p()
    test_add_mod_n()
    test_sub_mod_n()
    print("\n域运算门级测试全部通过 ✓")
