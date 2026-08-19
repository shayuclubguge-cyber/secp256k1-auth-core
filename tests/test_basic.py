"""
测试：基础门电路 + 仿真器验证
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout import Circuit
from tapeout.simulator import Simulator


def test_nand():
    c = Circuit("nand_test", n_in=2, n_out=1)
    a, b = c.input_signals()
    out = c.nand(a, b)
    assert c.to_bytes() != b""
    print(f"NAND 测试电路: {c}")

    sim = Simulator(c)
    assert sim.eval([False, False]) == [True]
    assert sim.eval([False, True]) == [True]
    assert sim.eval([True, False]) == [True]
    assert sim.eval([True, True]) == [False]
    print("✓ NAND 真值表通过")


def test_xor():
    c = Circuit("xor_test", n_in=2, n_out=1)
    a, b = c.input_signals()
    out = c.xor(a, b)

    sim = Simulator(c)
    assert sim.eval([False, False]) == [False]
    assert sim.eval([False, True]) == [True]
    assert sim.eval([True, False]) == [True]
    assert sim.eval([True, True]) == [False]
    print("✓ XOR 真值表通过")


def test_full_adder():
    c = Circuit("full_adder", n_in=3, n_out=2)
    a, b, cin = c.input_signals()
    sum_, cout = c.adder(a, b, cin)

    sim = Simulator(c)
    sim.set_outputs([sum_.id, cout.id])

    tests = [
        (0, 0, 0, 0, 0),
        (0, 0, 1, 1, 0),
        (0, 1, 0, 1, 0),
        (0, 1, 1, 0, 1),
        (1, 0, 0, 1, 0),
        (1, 0, 1, 0, 1),
        (1, 1, 0, 0, 1),
        (1, 1, 1, 1, 1),
    ]
    for av, bv, cinv, exp_s, exp_c in tests:
        result = sim.eval([bool(av), bool(bv), bool(cinv)])
        assert result == [bool(exp_s), bool(exp_c)], \
            f"FAIL: {av}+{bv}+{cinv} = {result}, expected [{exp_s},{exp_c}]"
    print("✓ 全加器真值表通过")


def test_mux():
    c = Circuit("mux_test", n_in=3, n_out=1)
    sel, a, b = c.input_signals()
    out = c.mux(sel, a, b)

    sim = Simulator(c)
    assert sim.eval([False, True, False]) == [True]
    assert sim.eval([False, False, True]) == [False]
    assert sim.eval([True, True, False]) == [False]
    assert sim.eval([True, False, True]) == [True]
    print("✓ MUX 测试通过")


def test_latch():
    c = Circuit("latch_test", n_in=1, n_out=1)
    d = c.input_signals()[0]
    out = c.latch(d)

    sim = Simulator(c)
    assert sim.beat([True]) == [False]
    assert sim.beat([False]) == [True]
    assert sim.beat([False]) == [False]
    print("✓ LATCH 时序测试通过")


def test_gate_count():
    c = Circuit("gate_count", n_in=2, n_out=1)
    a, b = c.input_signals()
    _ = c.xor(a, b)
    assert c.gate_count() == 4, f"预期 4, 实际 {c.gate_count()}""预期 5, 实际 {c.gate_count()}"
    print(f"✓ 门数统计: {c.gate_count()}")


if __name__ == "__main__":
    test_nand()
    test_xor()
    test_full_adder()
    test_mux()
    test_latch()
    test_gate_count()
    print("\n所有基础测试通过 ✓")
