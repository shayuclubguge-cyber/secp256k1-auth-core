"""
阶段4端到端测试：
  1. RT 模型全算法验证（keccak 已知答案、点运算、ecrecover 真实地址）
  2. 门级旗舰核（ecrecover_core）LOAD/ADD/SUB/MUL/READ 全通路逐拍仿真
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

import random

from tapeout.simulator import Simulator
from tapeout.field import P, N, int_to_bits, bits_to_int
from tapeout.engine import (
    keccak256, ecrecover, make_signature, scalar_mul, point_to_affine,
    hw_invmod, hw_mulmod, G,
)
from tapeout.ecrecover_core import build_ecrecover_core


# ---------------------------------------------------------------- RT 模型 --

def test_keccak_known_answers():
    assert keccak256(b"").hex() == \
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == \
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    print("✓ keccak-256 已知答案（空串 / \"abc\"）")


def test_hw_invmod():
    random.seed(11)
    for _ in range(20):
        a = random.randrange(1, P)
        inv = hw_invmod(a)
        assert hw_mulmod(a, inv) == 1
    print("✓ hw_invmod 费马求逆 20 组（a * a^-1 ≡ 1 mod p）")


def test_scalar_mul_known_addresses():
    # 公开已知向量：私钥 1 / 2 的以太坊地址
    def addr_of(d):
        x, y = point_to_affine(scalar_mul(d))
        return keccak256(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:].hex()
    assert addr_of(1) == "7e5f4552091a69125d5dfcb7b8c2659029395bdf"
    assert addr_of(2) == "2b5ad5c4795c026514f8317c7a215e218dccd6cf"
    print("✓ 标量乘：私钥 1/2 公开地址匹配")


def test_ecrecover_end_to_end():
    z = int.from_bytes(keccak256(b"hello tapeout"), "big")
    cases = [(1, 2), (0xC0FFEE, 0xDEADBEEF), (0x1234567890ABCDEF, 3)]
    random.seed(12)
    cases += [(random.randrange(1, N), random.randrange(1, N)) for _ in range(2)]
    for d, k in cases:
        r, s, v = make_signature(z, d, k)
        res = ecrecover(z, r, s, v)
        assert res is not None
        expect = point_to_affine(scalar_mul(d))
        assert (res[0], res[1]) == expect, f"d={d:#x} 恢复点不匹配"
    # 与公开地址对照（d=1）
    r, s, v = make_signature(z, 1, 2)
    assert ecrecover(z, r, s, v)[2].hex() == \
        "7e5f4552091a69125d5dfcb7b8c2659029395bdf"
    print(f"✓ ecrecover 端到端 {len(cases)} 组（含私钥1公开地址对照）")


def test_ecrecover_invalid():
    z = int.from_bytes(keccak256(b"hello tapeout"), "big")
    r, s, v = make_signature(z, 1, 2)
    assert ecrecover(z, 0, s, v) is None        # r=0 非法
    assert ecrecover(z, r, 0, v) is None        # s=0 非法
    assert ecrecover(z, r, s, v ^ 1) is not None  # 翻转奇偶 → 恢复出另一把钥匙
    wrong = ecrecover(z, r, s, v ^ 1)
    assert wrong[0] != point_to_affine(scalar_mul(1))[0] or \
           wrong[1] != point_to_affine(scalar_mul(1))[1]
    print("✓ 非法签名拒绝 / 奇偶翻转恢复出不同钥匙")


# ------------------------------------------------------------- 门级旗舰核 --

def _pins(op=0, start=0, load_sel=0, load_en=0, data=0, rd_sel=0):
    return (int_to_bits(op, 2) + [bool(start)] + int_to_bits(load_sel, 2)
            + [bool(load_en)] + int_to_bits(data, 64) + int_to_bits(rd_sel, 2))


def test_core_gate_level():
    c, data_out, busy_sig = build_ecrecover_core()
    print(f"  旗舰核: {c}")
    sim = Simulator(c)
    sim.set_outputs([s.id for s in data_out] + [busy_sig.id])

    def load_reg(sel, val):
        for i in range(4):  # LSB 字优先
            sim.beat(_pins(load_sel=sel, load_en=1, data=(val >> (64 * i)) & ((1 << 64) - 1)))

    def read_r():
        # R 在上一拍写入，本拍可读；连续4拍读4个字
        words = []
        for i in range(4):
            out = sim.beat(_pins(rd_sel=i))
            words.append(bits_to_int(out[:64]))
        return sum(v << (64 * i) for i, v in enumerate(words))

    random.seed(13)
    a = random.randrange(P)
    b = random.randrange(P)

    # LOAD
    load_reg(0, a)
    load_reg(1, b)

    # ADD（1拍）→ 下一拍读
    sim.beat(_pins(op=2, start=1))
    got = read_r()
    assert got == (a + b) % P, f"ADD: got={got}, want={(a + b) % P}"
    print("  ✓ LOAD + ADD + READ 通路")

    # SUB
    sim.beat(_pins(op=3, start=1))
    got = read_r()
    assert got == (a - b) % P, f"SUB: got={got}, want={(a - b) % P}"
    print("  ✓ SUB 通路")

    # MUL（256拍迭代）
    sim.beat(_pins(op=1, start=1))
    for _ in range(256):
        sim.beat(_pins())
    busy_out = sim.beat(_pins())[64]
    assert busy_out == False, "MUL 后 busy 应为 0"
    got = read_r()
    assert got == (a * b) % P, f"MUL: got={got}, want={(a * b) % P}"
    print("  ✓ MUL 256拍迭代 + busy 协议")
    print("✓ 旗舰核门级全通路测试通过")


if __name__ == "__main__":
    test_keccak_known_answers()
    test_hw_invmod()
    test_scalar_mul_known_addresses()
    test_ecrecover_end_to_end()
    test_ecrecover_invalid()
    test_core_gate_level()
    print("\n阶段4端到端测试全部通过 ✓")
