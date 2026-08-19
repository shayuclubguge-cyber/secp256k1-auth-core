"""
U256 模运算核 —— secp256k1 素数域 p = 2^256 - 2^32 - 977
架构：64位 limb 数据通路，NAND-only 实现
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from typing import List, Tuple
from tapeout import Circuit, Signal
from tapeout.simulator import Simulator


# secp256k1 素数 p（小端 limb 表示）
P_LIMBS = [
    0xFFFFFFFEFFFFFC2F,  # limb0 (低64位)
    0xFFFFFFFFFFFFFFFF,  # limb1
    0xFFFFFFFFFFFFFFFF,  # limb2
    0xFFFFFFFFFFFFFFFF,  # limb3 (高64位)
]


def build_adder_64() -> Tuple[Circuit, List[Signal]]:
    """64位行波进位加法器: a + b + cin → (sum, cout)"""
    c = Circuit("adder64", n_in=129, n_out=65)
    pins = c.input_signals()
    a = pins[0:64]
    b = pins[64:128]
    cin = pins[128]

    sum_out = []
    cout = cin
    for i in range(64):
        s, cout = c.adder(a[i], b[i], cout)
        sum_out.append(s)

    # 输出封存：保证 65 个输出恰好占据电路最后 65 个信号位
    return c, c.seal_outputs(sum_out + [cout])


def build_u256_add_mod() -> Tuple[Circuit, List[Signal]]:
    """
    U256 模 p 加法器
    引脚: 512入（a:256 + b:256）→ 256出（(a+b) mod p）
    快速约减：2^256 ≡ 2^32 + 977 (mod p)
    """
    c = Circuit("u256_add_mod", n_in=512, n_out=256)
    pins = c.input_signals()
    a = [pins[i * 64:(i + 1) * 64] for i in range(4)]
    b = [pins[256 + i * 64:256 + (i + 1) * 64] for i in range(4)]

    # p 的位表示
    p_bits = []
    for j in range(4):
        for i in range(64):
            p_bits.append(bool(P_LIMBS[j] & (1 << i)))
    p_sigs = [c.one() if b else c.zero() for b in p_bits]

    # r = 2^32 + 977 的位表示
    r = 2**32 + 977
    r_bits = [(r >> i) & 1 == 1 for i in range(256)]
    r_sigs = [c.one() if b else c.zero() for b in r_bits]

    # a + b（U257：256位 + 进位）
    sum_limbs = []
    cout = c.zero()
    for j in range(4):
        for i in range(64):
            s, cout = c.adder(a[j][i], b[j][i], cout)
            sum_limbs.append(s)

    # 快速约减：如果 cout=1，adjusted = sum_low + r；否则 adjusted = sum_low
    # r = 2^32 + 977，且 2^256 ≡ r (mod p)
    adjusted = []
    carry = c.zero()
    for i in range(256):
        # s = sum_low[i] + r[i]（当cout=1时）或 sum_low[i]（当cout=0时）
        # 用MUX选择是否加r
        r_or_zero = c.mux(cout, c.zero(), r_sigs[i])
        s, carry = c.adder(sum_limbs[i], r_or_zero, carry)
        adjusted.append(s)

    # 检查 adjusted >= p（第二次约减）
    diff_limbs = []
    bout = c.zero()
    for j in range(4):
        for i in range(64):
            idx = j * 64 + i
            s1 = c.xor(adjusted[idx], p_sigs[idx])
            diff = c.xor(s1, bout)
            na = c.not_(adjusted[idx])
            t1 = c.and_(na, p_sigs[idx])
            ns1 = c.not_(s1)
            t2 = c.and_(bout, ns1)
            bout = c.or_(t1, t2)
            diff_limbs.append(diff)

    need_reduce = c.not_(bout)

    outputs = []
    for i in range(256):
        out = c.mux(need_reduce, adjusted[i], diff_limbs[i])
        outputs.append(out)

    # 输出封存：保证 256 个输出恰好占据电路最后 256 个信号位
    outputs = c.seal_outputs(outputs)
    return c, outputs


def test_adder64():
    """测试64位加法器"""
    import random

    c, outputs = build_adder_64()
    print(f"64位加法器: {c}")

    sim = Simulator(c)
    sim.set_outputs([s.id for s in outputs])

    for _ in range(20):
        av = random.randint(0, (1 << 64) - 1)
        bv = random.randint(0, (1 << 64) - 1)
        cin = random.randint(0, 1)

        ref_sum = (av + bv + cin) & ((1 << 64) - 1)
        ref_cout = 1 if (av + bv + cin) >= (1 << 64) else 0

        a_bits = [(av >> i) & 1 == 1 for i in range(64)]
        b_bits = [(bv >> i) & 1 == 1 for i in range(64)]

        result = sim.eval(a_bits + b_bits + [bool(cin)])
        sum_bits = result[:64]
        cout = result[64]

        sum_val = sum(int(b) << i for i, b in enumerate(sum_bits))

        assert sum_val == ref_sum, f"FAIL: sum={sum_val}, expected={ref_sum}"
        assert cout == bool(ref_cout), f"FAIL: cout={cout}, expected={ref_cout}"

    print("✓ 64位加法器 20组随机向量通过")


def test_u256_add_mod():
    """测试U256模p加法器"""
    import random

    c, outputs = build_u256_add_mod()
    print(f"U256模p加法器: {c}")

    sim = Simulator(c)
    sim.set_outputs([s.id for s in outputs])

    p = (1 << 256) - (1 << 32) - 977

    for trial in range(10):
        av = random.randint(0, p - 1)
        bv = random.randint(0, p - 1)

        ref = (av + bv) % p

        a_bits = [(av >> i) & 1 == 1 for i in range(256)]
        b_bits = [(bv >> i) & 1 == 1 for i in range(256)]

        result = sim.eval(a_bits + b_bits)
        result_val = sum(int(b) << i for i, b in enumerate(result))

        if result_val != ref:
            print(f"FAIL on trial {trial}: a={av}, b={bv}")
            print(f"  ref={ref}")
            print(f"  result={result_val}")
            print(f"  diff={result_val - ref}")
            sum_ab = av + bv
            print(f"  av+bv={sum_ab}, cout={sum_ab >> 256}")
            raise AssertionError(f"U256 add mod failed")

    print("✓ U256模p加法器 10组随机向量通过")


if __name__ == "__main__":
    test_adder64()
    test_u256_add_mod()
    print("\nU256模运算基础测试通过 ✓")
