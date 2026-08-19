"""
时序模 p 乘法器门级测试（LATCH 数据通路，逐拍仿真）

拍序：拍0 start=1 载入 a,b；拍1..256 start=0 迭代；之后 result 稳定。
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

import random
import time

from tapeout.simulator import Simulator
from tapeout.field import P, int_to_bits, bits_to_int
from tapeout.modmul import build_mulmod_seq


def run_mulmod(sim, a: int, b: int) -> int:
    """驱动一次完整乘法：载入 + 256 拍迭代 + 1 拍读出（LATCH 输出旧值，
    第256次迭代结果在第257拍才出现在输出端）"""
    sim.beat([True] + int_to_bits(a) + int_to_bits(b))
    idle = [False] * 513
    for _ in range(256):
        sim.beat(idle)
    return bits_to_int(sim.beat(idle))


def test_mulmod():
    c, out = build_mulmod_seq()
    print(f"时序模p乘法器: {c}")

    cases = [
        (0, 0), (1, 1), (P - 1, P - 1), (P - 1, 2),
        (1 << 255, 1 << 255), (12345, 67890),
    ]
    random.seed(7)
    cases += [(random.randrange(P), random.randrange(P)) for _ in range(2)]

    sim = Simulator(c)
    sim.set_outputs([s.id for s in out])
    for i, (a, b) in enumerate(cases):
        t0 = time.time()
        res = run_mulmod(sim, a, b)
        ref = (a * b) % P
        dt = time.time() - t0
        assert res == ref, f"case{i}: a={a}, b={b}, got={res}, want={ref}"
        print(f"  case{i}: ok ({dt:.1f}s/257拍)")
        sim.reset()
    print("✓ 时序模p乘法器通过（边界 6 + 随机 2，逐拍门级仿真）")


if __name__ == "__main__":
    test_mulmod()
    print("\n模乘法器门级测试通过 ✓")
