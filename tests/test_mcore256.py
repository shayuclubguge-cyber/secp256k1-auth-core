"""mcore256 模乘引擎验证：生成 → 指令数/引脚检查 → 与黄金模型对拍"""

import random
import sys

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")

from tapeout.field import P, N
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.fadd64 import build_fadd64
from tapeout.parts.mcore256 import build_mcore256, MCoreDriver

# v2 链上参数（fadd64 = v2 cid1）；本地仿真以同键注册，网表与链上版一致
V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
V2_FADD64_CID = 1

MASK = (1 << 256) - 1


def make_driver():
    resolver = REFResolver()
    resolver.register(V2_CPU, V2_FADD64_CID, build_fadd64())
    core = build_mcore256(V2_CPU, V2_FADD64_CID)
    sim = SimWithREF(core, resolver)
    return core, sim, MCoreDriver(sim)


def test_structure():
    core, sim, d = make_driver()
    n_inst = len(core.instructions)
    n_latch = sum(1 for i in core.instructions if i[0] == 1)
    n_nand = sum(1 for i in core.instructions if i[0] == 0)
    n_ref = sum(1 for i in core.instructions if i[0] == 2)
    n_state = n_latch + 257  # 子电路 fadd64 状态穿透（256 ACC + 1 carry）
    print(f"指令总数={n_inst}（NAND={n_nand}, LATCH={n_latch}, REF={n_ref}）"
          f" 状态位={n_state} 网表={len(core.to_bytes())}B")
    assert n_inst <= 3500, "超 OKX 流片硬顶"
    assert len(core.to_bytes()) <= 24_575, "超 SSTORE2 单块上限"
    assert core.n_in <= 255 and core.n_out <= 255
    print("✓ 结构约束全部满足（≤3,500 指令 / ≤24,575B / ≤255 引脚）")


def check_mul(d, a, b, m, label=""):
    got = d.mulmod(a, b, m)
    want = (a * b) % m
    assert got == want, f"mulmod 不匹配 {label}: got={got:#x} want={want:#x}"
    return d.run_ticks


def test_mulmod_p():
    _, _, d = make_driver()
    ticks = []
    # 边界向量
    for a, b, tag in [
        (0, 0, "0·0"), (1, 1, "1·1"), (0, P - 1, "0·(p-1)"),
        (1, P - 1, "1·(p-1)"), (P - 1, P - 1, "(p-1)²"),
        (2, P - 2, "2·(p-2)"), (1 << 255, 1 << 255, "2^255·2^255"),
        (P - 1, 1 << 255, "(p-1)·2^255"),
        (MASK, MASK, "2^256-1 平方（非规范输入）"),   # 输入可 ≥m（<2^256）
        (0xC0FFEE, 0xDEADBEEF, "小常数"),
    ]:
        ticks.append(check_mul(d, a, b, P, tag))
    # 随机向量
    rng = random.Random(20260818)
    for i in range(4):
        a, b = rng.getrandbits(256), rng.getrandbits(256)
        ticks.append(check_mul(d, a, b, P, f"rand{i}"))
    print(f"✓ mod-p 模乘 14/14 全对（RUN 拍数 {min(ticks)}~{max(ticks)}）")


def test_mulmod_n():
    _, _, d = make_driver()
    rng = random.Random(777)
    for a, b, tag in [(1, N - 1, "1·(n-1)"), (N - 1, N - 1, "(n-1)²"),
                      (0x76d2fdf1302d1fa9556f4df94ec84cefba6d482e54f47c6c2a238c1baa560f0e,
                       0x9a95aba4ca4bd7d78b95db39afa12c7bbd2b9b739e8fd148092da7632cd03e89,
                       "r·s（演示向量）")]:
        check_mul(d, a, b, N, tag)
    for i in range(2):
        check_mul(d, rng.getrandbits(256), rng.getrandbits(256), N, f"rand{i}")
    print("✓ mod-n 模乘 5/5 全对（同一电路换 rc 即换模数）")


def test_noncanonical_output():
    """RUN 原始输出（REDUCE 前）< 2^256 且 ≡ (mod m)"""
    _, _, d = make_driver()
    rng = random.Random(42)
    for i in range(3):
        a, b = rng.getrandbits(256), rng.getrandbits(256)
        rc = (1 << 256) - P
        d.load_c(rc)
        d.load_a(a % P)
        d.load_b(b)
        d.run()
        R = d.read()                      # REDUCE 前的原始值
        assert R < (1 << 256) and R % P == (a * b) % P, f"非规范输出错误 #{i}"
        d.load_c(P)
        d.reduce()
        assert d.read() == (a * b) % P, f"REDUCE 后不规范 #{i}"
    print("✓ 非规范输出不变式 + REDUCE 规范化均成立")


if __name__ == "__main__":
    test_structure()
    test_mulmod_p()
    test_mulmod_n()
    test_noncanonical_output()
    print("\nALL PASSED")
