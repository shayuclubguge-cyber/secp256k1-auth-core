"""piso256 逐拍验证：装入 → 位流 → 内容自恢复 → fuzz"""

import hashlib
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout.parts.piso256 import (
    build_piso256, load_bits, stream_bits, idle_beats,
)
from tapeout.simulator import Simulator


def run_stream(sim):
    """跑 256 拍流出窗口，还原为整数（MSB 先行）"""
    v = 0
    for ins in stream_bits():
        out = sim.beat(ins)
        v = (v << 1) | int(out[0])
    return v


def main():
    c = build_piso256()
    blob = c.to_bytes()
    nand = sum(1 for x in c.instructions if x[0] == 0)
    latch = sum(1 for x in c.instructions if x[0] == 1)
    print(f"piso256: {nand} NAND, {latch} LATCH, {len(c.instructions)} 指令, "
          f"{len(blob)}B, sha256={hashlib.sha256(blob).hexdigest()[:16]}")
    assert len(blob) <= 24575, "超 OKX 单块上限！"
    assert c.n_in == 66 and c.n_out == 1

    # 固定向量 + 自恢复
    sim = Simulator(c)
    val = 0xDEADBEEF12345678_0123456789ABCDEF_FEDCBA9876543210_0F1E2D3C4B5A6978
    for ins in idle_beats(8):            # 上电空转（4 的倍数，网格纪律）
        sim.beat(ins)
    for ins in load_bits(val):
        sim.beat(ins)
    got1 = run_stream(sim)
    assert got1 == val, f"首次流出不符: {got1:064x} != {val:064x}"
    print("✓ 装入→流出 256 位全对（MSB 先行）")

    for ins in idle_beats(12):
        sim.beat(ins)
    got2 = run_stream(sim)
    assert got2 == val, "流出后内容未自恢复！"
    print("✓ 二次流出一致（rot1×256 内容自恢复）")

    # 装入期间/空闲期引脚垃圾不干扰
    sim2 = Simulator(c)
    rng = random.Random(42)
    vals = [rng.getrandbits(256) for _ in range(20)]
    for i, v in enumerate(vals):
        for ins in idle_beats(4 * rng.randint(0, 3)):
            sim2.beat(ins)
        for ins in load_bits(v):
            sim2.beat(ins)
        got = run_stream(sim2)
        assert got == v, f"fuzz #{i} 失败: {got:064x} != {v:064x}"
    print("✓ fuzz 20/20（随机空闲间隔 + 随机值）")

    # 全 0 / 全 1 边界
    for v in (0, (1 << 256) - 1, 1, 1 << 255):
        sim3 = Simulator(c)
        for ins in load_bits(v):
            sim3.beat(ins)
        assert run_stream(sim3) == v, f"边界值 {v:#x} 失败"
    print("✓ 边界值（0 / 全1 / 1 / 2^255）")

    print("\npiso256 全部通过")


if __name__ == "__main__":
    main()
