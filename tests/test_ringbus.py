"""ringbus5 逐拍验证：写入 → 相位对齐读出 → 持久性 → 隔离性 → fuzz"""

import hashlib
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout.parts.ringbus import (
    build_ringbus5, write_beats, read_input, idle_input, word_of, N_RINGS,
)
from tapeout.simulator import Simulator


def read_ring(sim, raddr: int, beats_used: list) -> int:
    """在 ph0 起的 4 拍窗口读回整个 256 位字（beat 计数即相位）"""
    ws = []
    for _ in range(4):
        assert beats_used[0] % 4 == len(ws), "读窗口必须 ph0 对齐"
        out = sim.beat(read_input(raddr))
        beats_used[0] += 1
        ws.append(word_of(out))
    return sum(w << (64 * i) for i, w in enumerate(ws))


def idle(sim, n: int, beats_used: list):
    for _ in range(n):
        sim.beat(idle_input())
        beats_used[0] += 1


def write(sim, ring: int, value: int, beats_used: list):
    assert beats_used[0] % 4 == 0, "写窗口必须 ph0 对齐"
    for ins in write_beats(ring, value):
        sim.beat(ins)
        beats_used[0] += 1


def main():
    c = build_ringbus5()
    blob = c.to_bytes()
    nand = sum(1 for x in c.instructions if x[0] == 0)
    latch = sum(1 for x in c.instructions if x[0] == 1)
    print(f"ringbus5: {nand} NAND, {latch} LATCH, {len(c.instructions)} 指令, "
          f"{len(blob)}B, sha256={hashlib.sha256(blob).hexdigest()[:16]}")
    assert len(blob) <= 24575, "超 OKX 单块上限！"
    assert c.n_in == 71 and c.n_out == 64

    sim = Simulator(c)
    bu = [0]

    # 5 环写入 + 相位对齐读回
    vals = [0x1111111111111111 * (i + 1) & ((1 << 256) - 1) for i in range(N_RINGS)]
    for r, v in enumerate(vals):
        write(sim, r, v, bu)
    for r in range(N_RINGS):
        got = read_ring(sim, r, bu)
        assert got == vals[r], f"环{r} 读出不符: {got:064x} != {vals[r]:064x}"
    print("✓ 5 环写入→相位对齐读回全对")

    # 持久性：长空闲（4 的倍数拍）后内容不变
    idle(sim, 1000, bu)
    for r in range(N_RINGS):
        assert read_ring(sim, r, bu) == vals[r], f"环{r} 长空闲后内容漂移"
    print("✓ 1000 拍空闲后 5 环内容全部保持")

    # 隔离性：重写环2，其余不变
    newv = 0xABCDE01234567890_FEDCBA0987654321_5555555555555555_AAAAAAAAAAAAAAAA
    write(sim, 2, newv, bu)
    for r in range(N_RINGS):
        exp = newv if r == 2 else vals[r]
        assert read_ring(sim, r, bu) == exp, f"环{r} 被串写污染"
    print("✓ 单环重写不串扰")

    # fuzz：随机写/读/空闲混合
    rng = random.Random(7)
    cur = [0] * N_RINGS
    sim2 = Simulator(c)
    bu2 = [0]
    for step in range(60):
        op = rng.random()
        if op < 0.45:
            r = rng.randrange(N_RINGS)
            v = rng.getrandbits(256)
            write(sim2, r, v, bu2)
            cur[r] = v
        elif op < 0.8:
            r = rng.randrange(N_RINGS)
            got = read_ring(sim2, r, bu2)
            assert got == cur[r], f"fuzz#{step} 环{r}: {got:064x} != {cur[r]:064x}"
        else:
            idle(sim2, 4 * rng.randint(1, 5), bu2)
    print("✓ fuzz 60 步混合操作全对")

    print("\nringbus5 全部通过")


if __name__ == "__main__":
    main()
