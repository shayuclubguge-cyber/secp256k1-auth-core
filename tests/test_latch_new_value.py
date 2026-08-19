"""
测试LATCH语义变体：输出新值 vs 旧值
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout import Circuit
from tapeout.simulator import Simulator


class SimNewValue(Simulator):
    """LATCH输出新值（而非旧值）的仿真器"""

    def eval(self, inputs):
        assert len(inputs) == self.circuit.n_in
        self.state = {0: False, 1: True}
        for i, val in enumerate(inputs):
            self.state[2 + i] = val

        for inst in self.circuit.instructions:
            op = inst[0]
            if op == 0:  # NAND
                _, a, b = inst
                val = not (self._get(a) and self._get(b))
                self.state[len(self.state)] = val
            elif op == 1:  # LATCH
                _, d = inst
                # 输出d的当前值（而非旧值）
                new_val = self._get(d)
                self.state[len(self.state)] = new_val
                self.latch_values[len(self.state) - 1] = new_val
            elif op == 2:
                raise NotImplementedError("REF")

        out_signals = list(self.state.keys())[-self.circuit.n_out:]
        return [self.state[s] for s in out_signals]


def test_new_value_latch():
    """如果LATCH输出新值，反馈回路是否可行？"""
    c = Circuit("feedback_new", n_in=1, n_out=1)
    inp = c.input_signals()[0]

    # LATCH，d初始为输入引脚
    latch = c.latch(inp)

    # NAND：LATCH AND 1 → NOT(LATCH)
    nand_out = c.nand(latch, c.one())

    # 输出 = NAND
    c.n_out = 1

    print("=== LATCH输出新值 ===")
    sim = SimNewValue(c)
    for i in range(3):
        r = sim.beat([True])
        print(f"拍{i+1}: 输入1, 输出={r}, latch={sim.latch_values}")

    print("\n=== LATCH输出旧值（标准） ===")
    sim2 = Simulator(c)
    for i in range(3):
        r = sim2.beat([True])
        print(f"拍{i+1}: 输入1, 输出={r}, latch={sim2.latch_values}")


def test_toggling_with_new_value():
    """用新值语义测试翻转"""
    c = Circuit("toggle_new", n_in=0, n_out=2)

    # 初始输入=0
    latch_a = c.latch(c.zero())
    not_a = c.nand(latch_a, c.one())
    latch_b = c.latch(not_a)
    not_b = c.nand(latch_b, c.one())

    c.n_out = 2

    print("\n=== 翻转 - 新值语义 ===")
    sim = SimNewValue(c)
    for i in range(5):
        r = sim.beat([])
        print(f"拍{i+1}: A={r[0]}, B={r[1]}, latch={sim.latch_values}")

    print("\n=== 翻转 - 旧值语义 ===")
    sim2 = Simulator(c)
    for i in range(5):
        r = sim2.beat([])
        print(f"拍{i+1}: A={r[0]}, B={r[1]}, latch={sim2.latch_values}")


if __name__ == "__main__":
    test_new_value_latch()
    test_toggling_with_new_value()
