"""
流式 Keccak 组件 —— χ通道 + θ列处理器
≤255引脚，可REF，填补老橘子的零件库空位
"""

import sys
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from typing import List, Tuple
from tapeout import Circuit, Signal
from tapeout.simulator import Simulator


def build_chi_lane() -> Tuple[Circuit, List[Signal]]:
    """
    χ 通道单lane处理器（等效老橘子 #22）
    引脚: 192入（3×64位lane）→ 64出
    功能: out[i] = a[i] XOR (NOT(b[i]) AND c[i])
    """
    c = Circuit("chi_lane", n_in=192, n_out=64)
    pins = c.input_signals()
    a = pins[0:64]
    b = pins[64:128]
    c_in = pins[128:192]

    outputs = []
    for i in range(64):
        nb = c.not_(b[i])
        t = c.and_(nb, c_in[i])
        out = c.xor(a[i], t)
        outputs.append(out)

    return c, outputs


def build_xor3_lane() -> Tuple[Circuit, List[Signal]]:
    """
    XOR3 单lane处理器（等效老橘子 #20）
    引脚: 192入（3×64位）→ 64出
    功能: out = a XOR b XOR c
    """
    circ = Circuit("xor3_lane", n_in=192, n_out=64)
    pins = circ.input_signals()
    a = pins[0:64]
    b = pins[64:128]
    c_in = pins[128:192]

    outputs = []
    for i in range(64):
        t1 = circ.xor(a[i], b[i])
        out = circ.xor(t1, c_in[i])
        outputs.append(out)

    return circ, outputs


def build_keccak_component() -> Tuple[Circuit, List[Signal]]:
    """
    Keccak 流式组件 MVP
    引脚: 204（3×64位lane + 5位lane_id + 5位round + 2位mode）
    输出: 64位结果

    mode:
      00 = χ（chi）: 输入同行3个lane，输出χ结果
      01 = θ_step: 输入3个lane做XOR3，输出部分列校验
      10 = RC: 输入5位round，输出64位轮常数
    """
    c = Circuit("keccak_component", n_in=204, n_out=64)
    pins = c.input_signals()
    lane_a = pins[0:64]
    lane_b = pins[64:128]
    lane_c = pins[128:192]
    lane_id = pins[192:197]
    round_sigs = pins[197:202]
    mode = pins[202:204]

    # --- χ 模式 ---
    chi_out = []
    for i in range(64):
        nb = c.not_(lane_b[i])
        t = c.and_(nb, lane_c[i])
        chi_out.append(c.xor(lane_a[i], t))

    # --- θ_step 模式（XOR3）---
    xor3_out = []
    for i in range(64):
        t1 = c.xor(lane_a[i], lane_b[i])
        xor3_out.append(c.xor(t1, lane_c[i]))

    # --- RC 模式（简化：固定轮常数 RC[0]）---
    from test_keccak import KECCAK_RC
    rc = KECCAK_RC[0]
    rc_out = []
    for i in range(64):
        if rc & (1 << i):
            rc_out.append(c.one())
        else:
            rc_out.append(c.zero())

    # --- 模式选择 MUX ---
    is_chi = c.and_(c.not_(mode[0]), c.not_(mode[1]))
    is_theta = c.and_(mode[0], c.not_(mode[1]))
    is_rc = c.and_(c.not_(mode[0]), mode[1])

    outputs = []
    for i in range(64):
        # 三级MUX: is_chi ? chi_out : (is_theta ? xor3_out : rc_out)
        t1 = c.mux(is_chi, rc_out[i], chi_out[i])  # chi=1选chi, 否则选rc
        t2 = c.mux(is_theta, t1, xor3_out[i])      # theta=1选xor3, 否则选t1
        outputs.append(t2)

    # 确保输出在末尾：两阶段封存（buf 会交错，seal_outputs 才保证占据最后 nOut 位）
    final_outputs = c.seal_outputs(outputs)
    c.n_out = len(final_outputs)

    return c, final_outputs


def test_chi_lane():
    """测试χ通道与参考实现对拍"""
    import random

    c, outputs = build_chi_lane()
    print(f"χ通道电路: {c}")

    sim = Simulator(c)
    sim.set_outputs([s.id for s in outputs])

    for _ in range(10):
        a = [random.choice([False, True]) for _ in range(64)]
        b = [random.choice([False, True]) for _ in range(64)]
        c_in = [random.choice([False, True]) for _ in range(64)]

        # 参考结果
        ref = [a[i] ^ ((not b[i]) and c_in[i]) for i in range(64)]

        # 电路仿真
        result = sim.eval(a + b + c_in)

        assert result == ref, f"FAIL: χ结果不匹配"

    print("✓ χ通道 10组随机向量通过")


def test_xor3_lane():
    """测试XOR3通道"""
    import random

    c, outputs = build_xor3_lane()
    print(f"XOR3电路: {c}")

    sim = Simulator(c)
    sim.set_outputs([s.id for s in outputs])

    for _ in range(10):
        a = [random.choice([False, True]) for _ in range(64)]
        b = [random.choice([False, True]) for _ in range(64)]
        c_in = [random.choice([False, True]) for _ in range(64)]

        ref = [a[i] ^ b[i] ^ c_in[i] for i in range(64)]
        result = sim.eval(a + b + c_in)

        assert result == ref

    print("✓ XOR3 10组随机向量通过")


def test_keccak_component():
    """测试组合组件"""
    c, outputs = build_keccak_component()
    print(f"Keccak组件: {c}")

    sim = Simulator(c)
    sim.set_outputs([s.id for s in outputs])

    import random

    # 测试χ模式
    for _ in range(5):
        a = [random.choice([False, True]) for _ in range(64)]
        b = [random.choice([False, True]) for _ in range(64)]
        c_in = [random.choice([False, True]) for _ in range(64)]

        # mode = 00 (chi)
        inputs = a + b + c_in + [False] * 5 + [False] * 5 + [False, False]
        result = sim.eval(inputs)

        ref = [a[i] ^ ((not b[i]) and c_in[i]) for i in range(64)]
        assert result == ref

    print("✓ Keccak组件 χ模式通过")

    # 测试XOR3模式
    for _ in range(5):
        a = [random.choice([False, True]) for _ in range(64)]
        b = [random.choice([False, True]) for _ in range(64)]
        c_in = [random.choice([False, True]) for _ in range(64)]

        # mode = 01 (theta)
        inputs = a + b + c_in + [False] * 5 + [False] * 5 + [True, False]
        result = sim.eval(inputs)

        ref = [a[i] ^ b[i] ^ c_in[i] for i in range(64)]
        assert result == ref

    print("✓ Keccak组件 θ模式通过")


if __name__ == "__main__":
    test_chi_lane()
    test_xor3_lane()
    test_keccak_component()
    print("\n所有流式Keccak组件测试通过 ✓")
