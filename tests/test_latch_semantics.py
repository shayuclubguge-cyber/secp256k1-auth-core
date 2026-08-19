"""
测试LATCH前向引用和反馈回路
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout import Circuit
from tapeout.simulator import Simulator


def test_forward_ref():
    """测试LATCH前向引用：LATCH在NAND之前，d引用NAND"""
    c = Circuit("forward_ref", n_in=1, n_out=1)
    inp = c.input_signals()[0]

    # LATCH，d引用后续信号（占位）
    # 先创建LATCH，d=2（输入引脚，作为占位）
    latch = c.latch(inp)

    # NAND，引用LATCH
    nand_out = c.nand(latch, c.one())

    # 输出 = NAND
    c.n_out = 1

    sim = Simulator(c)
    # 拍1：输入1，LATCH输出0（初始），NAND=NOT(0 AND 1)=1，LATCH存储d=1（输入值）
    r = sim.beat([True])
    print(f"拍1: 输入1, 输出={r}, latch={sim.latch_values}")

    # 拍2：输入0，LATCH输出1（拍1存储），NAND=NOT(1 AND 1)=0，LATCH存储d=0
    r = sim.beat([False])
    print(f"拍2: 输入0, 输出={r}, latch={sim.latch_values}")


def test_feedback_loop():
    """测试反馈回路：LATCH → NAND → LATCH"""
    c = Circuit("feedback", n_in=1, n_out=1)
    inp = c.input_signals()[0]

    # LATCH_A，初始d=0
    latch_a = c.latch(c.zero())

    # NAND：LATCH_A AND inp
    nand_out = c.nand(latch_a, inp)

    # 创建另一个电路，LATCH_B的d=NAND输出
    # 但这里LATCH_A的d固定为0，不会更新

    # 输出 = NAND
    c.n_out = 1

    sim = Simulator(c)
    # 拍1：输入1，LATCH_A=0，NAND=1
    r = sim.beat([True])
    print(f"拍1: 输入1, 输出={r}")

    # 拍2：输入1，LATCH_A=0（没变），NAND=1
    r = sim.beat([True])
    print(f"拍2: 输入1, 输出={r}")

    # 没有反馈，因为LATCH_A的d=0


def test_toggling_latch():
    """测试翻转LATCH：使用两个LATCH交替"""
    c = Circuit("toggle", n_in=0, n_out=2)

    # LATCH_A，d=1（置位）
    latch_a = c.latch(c.one())

    # NOT(LATCH_A)
    not_a = c.nand(latch_a, c.one())

    # LATCH_B，d=NOT(LATCH_A)
    latch_b = c.latch(not_a)

    # NOT(LATCH_B)
    not_b = c.nand(latch_b, c.one())

    # 输出A和B
    c.n_out = 2

    sim = Simulator(c)
    for i in range(5):
        r = sim.beat([])
        print(f"拍{i+1}: A={r[0]}, B={r[1]}, latch={sim.latch_values}")


if __name__ == "__main__":
    print("=== 前向引用测试 ===")
    test_forward_ref()
    print("\n=== 反馈回路测试 ===")
    test_feedback_loop()
    print("\n=== 翻转LATCH测试 ===")
    test_toggling_latch()
