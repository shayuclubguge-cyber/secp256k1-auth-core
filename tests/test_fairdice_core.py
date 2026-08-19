"""
FairDice Core 全量本地对拍 —— 通过 fairdice_ctrl 主控壳（REF×4）驱动

向量构成（与任务书一致）：
  1. 10 组有效签名（不同消息、不同地址、不同区块高度）：
     a) 签名验证 —— 双层证据：
        · 门级结构校验（1 ≤ r,s < n，经 ecrecover_ctrl 真实网表）+ MUL 锚点
          （r·s mod n 回读对拍，打通 AUTH 单元内 mcore/piso/fadd 全通路）
        · 完整 ECDSA 恢复 —— 编译器影子执行（compile_ecrecover 宏程序）
          对照 engine.ecrecover 黄金模型，恢复地址 == 签名者地址
        （完整 ecrecover 宏程序含 9,487 条 MUL，门级全量约 8 小时/组，
        与 ECREC 自身发布同级：L1 门级 gadget + L2 编译器影子）
     b) 哈希种子 —— 门级 kl_top 全流程，== engine.keccak256(r‖s)
     c) mod 6 + 1 —— 门级 fadd64 混合高度 + piso256 位流 + 壳内累加器，
        点数 == (seed + height) mod 2^256 mod 6 + 1 ∈ 1..6
  2. 边界：r=0 / s=0 / r≥n / s≥n / 全零输入 → fail 置位；
     r=s=n−1（极大合法值）→ 全流程正常；r‖s=0xFF×64（极大数值）→ 正常取模；
     伪造签名（s+1）→ 恢复地址不匹配（应用层检出）
  3. 字节指纹：壳 sha256 + REF×4 目标解析 + 复用零件指纹对照链上记录

通过标准：10/10 + 边界全过。
"""

import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tapeout import engine
from tapeout.field import N
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.fadd64 import build_fadd64
from tapeout.parts.mcore256 import build_mcore256
from tapeout.parts.piso256 import build_piso256
from tapeout.parts.ringbus import build_ringbus5
from tapeout.parts.ecrecover_ctrl import build_ecrecover_ctrl
from tapeout.parts.kl_split import (
    build_kl_ringa_lo, build_kl_ringa_hi, build_kl_rho,
    build_kl_ringb, build_kl_top,
)
from tapeout.parts.fairdice_ctrl import (
    build_fairdice_ctrl, FairDiceDriver, DEFAULT_REFS,
)
from tapeout.parts.ecrecover_compiler import Compiler
from test_keccak_component import build_keccak_component

# ECREC v2 处理器（BSC 主网）与零件 cid（CHAIN_ASSETS.md 定稿）
V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")

MASK256 = (1 << 256) - 1

# CHAIN_ASSETS.md 记录的链上指纹前缀（交叉验证用）
CHAIN_SHA = {
    "fadd64":      "c22f8c74b0def33c",   # cid1/cid6 同网表
    "mcore256":    "37eb121673dbf830",   # cid2
    "piso256":     "356cdf33ca964192",   # cid3
    "ringbus5":    "83beeeb0b50e4bae",   # cid4/cid5 同网表
    "keccak_comp": "2e0a1609fb8f",       # cid7
    "kl_ringa":    "5f62ced6d705",       # cid9/cid10 同构
    "kl_rho":      "a5dd3b52cb1b",       # cid11
    "kl_ringb":    "c2be8eefec19",       # cid12
    "kl_top":      "dccbd34519c3",       # cid13
}


# ---------------------------------------------------------------------------
# 环境构建
# ---------------------------------------------------------------------------

def make_shell():
    """注册 ECREC v2 全部零件真实网表（非函数式替身），构建 FairDice 壳"""
    resolver = REFResolver()
    resolver.register(V2_CPU, 1, build_fadd64())              # mcore256 内部引用
    resolver.register(V2_CPU, 2, build_mcore256(V2_CPU, 1))
    resolver.register(V2_CPU, 3, build_piso256())
    resolver.register(V2_CPU, 4, build_ringbus5())            # ringbusA
    resolver.register(V2_CPU, 5, build_ringbus5())            # ringbusB
    resolver.register(V2_CPU, 6, build_fadd64())              # fadd64b 同网表
    comp, _ = build_keccak_component()
    resolver.register(V2_CPU, 7, comp)
    resolver.register(V2_CPU, 8, build_ecrecover_ctrl(V2_CPU))
    ra_lo, _ = build_kl_ringa_lo()
    ra_hi, _ = build_kl_ringa_hi()
    rho, _ = build_kl_rho()
    rb, _ = build_kl_ringb()
    resolver.register(V2_CPU, 9, ra_lo)
    resolver.register(V2_CPU, 10, ra_hi)
    resolver.register(V2_CPU, 11, rho)
    resolver.register(V2_CPU, 12, rb)
    kl_top, _ = build_kl_top((V2_CPU, 7), (V2_CPU, 9), (V2_CPU, 10),
                             (V2_CPU, 12), (V2_CPU, 11))
    resolver.register(V2_CPU, 13, kl_top)
    core = build_fairdice_ctrl(V2_CPU, DEFAULT_REFS)
    return core, SimWithREF(core, resolver)


# ---------------------------------------------------------------------------
# 0. 结构约束 + 字节指纹交叉验证
# ---------------------------------------------------------------------------

def test_structure_and_fingerprints():
    core, _ = make_shell()
    blob = core.to_bytes()
    nand = sum(1 for i in core.instructions if i[0] == 0)
    latch = sum(1 for i in core.instructions if i[0] == 1)
    ref = sum(1 for i in core.instructions if i[0] == 2)
    sha = hashlib.sha256(blob).hexdigest()
    print(f"fairdice_ctrl: {core.n_in}→{core.n_out}, NAND={nand}, LATCH={latch}, "
          f"REF={ref}, {len(core.instructions)} 指令, {len(blob):,}B")
    print(f"  sha256 = {sha}")
    assert core.n_in <= 255 and core.n_out <= 255, "引脚超 255"
    assert len(blob) <= 24_575, "超 OKX SSTORE2 单块上限"
    assert len(blob) <= 39_000, "超 BEP-652 ~39KB 上限"
    assert len(core.instructions) <= 3_500, "超 OKX 流片指令硬顶"
    assert ref == 4, "应为 4 REF"
    assert nand + latch < 3_000, f"超 3,000 晶体管目标: {nand + latch}"
    est_gas = 421 * len(blob)
    assert est_gas <= 16_777_216, "超 BEP-652 单交易 gas 上限"
    print(f"✓ 结构约束全满足（≤255 引脚 / {len(blob):,}B ≤ 24,575B / "
          f"晶体管 {nand + latch} < 3,000 / 预估 gas {est_gas:,} ≪ BEP-652）")

    # REF 目标解析校验
    refs = [i for i in core.instructions if i[0] == 2]
    got = sorted((i[2]) for i in refs)
    assert all(i[1] == V2_CPU for i in refs), "REF 必须全部指向 ECREC v2"
    assert got == sorted([8, 13, 6, 3]), f"REF cid 不符: {got}"
    print(f"✓ REF×4 目标解析：cid {got}，均属 ECREC v2 处理器")

    # 存档网表与现场重建逐字节一致
    saved = (ROOT / "vectors" / "fairdice_ctrl.net").read_bytes()
    assert saved == blob, "vectors/fairdice_ctrl.net 与源码重建不一致"
    print("✓ 存档网表 = 源码现场重建（逐字节）")

    # 复用零件指纹交叉验证（源码重建 vs CHAIN_ASSETS 链上记录）
    comp, _ = build_keccak_component()
    ra_lo, _ = build_kl_ringa_lo()
    ra_hi, _ = build_kl_ringa_hi()
    rho, _ = build_kl_rho()
    rb, _ = build_kl_ringb()
    kl_top, _ = build_kl_top((V2_CPU, 7), (V2_CPU, 9), (V2_CPU, 10),
                             (V2_CPU, 12), (V2_CPU, 11))
    parts = {
        "fadd64": build_fadd64().to_bytes(),
        "mcore256": build_mcore256(V2_CPU, 1).to_bytes(),
        "piso256": build_piso256().to_bytes(),
        "ringbus5": build_ringbus5().to_bytes(),
        "keccak_comp": comp.to_bytes(),
        "kl_ringa": ra_lo.to_bytes(),
        "kl_rho": rho.to_bytes(),
        "kl_ringb": rb.to_bytes(),
        "kl_top": kl_top.to_bytes(),
    }
    assert ra_hi.to_bytes() == parts["kl_ringa"], "kl_ringa_hi 应与 lo 同构"
    for name, blob_p in parts.items():
        sha_p = hashlib.sha256(blob_p).hexdigest()
        assert sha_p.startswith(CHAIN_SHA[name]), \
            f"{name} 指纹漂移: {sha_p[:16]} != {CHAIN_SHA[name]}"
        print(f"✓ {name:<12} 指纹 {sha_p[:16]}… 与链上记录一致"
              f"（{len(blob_p):,}B）")

    # ecrecover_ctrl（AUTH 单元）指纹：存档网表比对
    ec_blob = build_ecrecover_ctrl(V2_CPU).to_bytes()
    saved_ec = (ROOT / "vectors" / "ecrecover_ctrl.net").read_bytes()
    assert ec_blob == saved_ec, "ecrecover_ctrl 与存档不一致"
    print(f"✓ ecrecover_ctrl  指纹 {hashlib.sha256(ec_blob).hexdigest()[:16]}… "
          f"与 vectors/ 存档一致（{len(ec_blob):,}B）")


# ---------------------------------------------------------------------------
# 1. 壳内 mod-6 累加器单元测试（穷举 + 特值）
# ---------------------------------------------------------------------------

def test_mod6_accumulator():
    _, sim = make_shell()
    d = FairDiceDriver(sim, N)
    # 穷举小值 0..23：覆盖 acc 全状态 × 输入位两值（Horner 每拍可达）
    for v in range(24):
        sim.reset()
        d.ticks = 0
        dice = d.stream_dice(v)
        assert dice == v % 6 + 1, f"v={v}: dice={dice} != {v % 6 + 1}"
    # 特值：全 1 / 2^255 / 2^256−1
    for v in (MASK256, 1 << 255, MASK256 - 1):
        sim.reset()
        d.ticks = 0
        dice = d.stream_dice(v)
        assert dice == v % 6 + 1, f"v={v:#x}: dice={dice} != {v % 6 + 1}"
    print("✓ 壳内 mod-6 累加器：穷举 0..23 + 特值 3 组全对"
          "（dice = v mod 6 + 1）")


# ---------------------------------------------------------------------------
# 2. 10 组有效签名全流程对拍
# ---------------------------------------------------------------------------

def _priv_addr(priv: int) -> bytes:
    """由私钥推导地址（黄金模型）"""
    from tapeout.engine import scalar_mul, point_to_affine, G, keccak256
    x, y = point_to_affine(scalar_mul(priv, G))
    return keccak256(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:]


def test_vectors_10(start: int = 0, end: int = 10):
    core, sim = make_shell()
    d = FairDiceDriver(sim, N)
    t0 = time.time()
    passed = 0
    for i in range(start, end):
        tv0 = time.time()
        priv = 0xC0FFEE + 0x12345 * (i + 1)
        k = 0xDEADBEEF + 0xABCDEF * (i + 1)
        z = int.from_bytes(engine.keccak256(
            f"FairDice Core test vector {i}".encode()), "big")
        r, s, v = engine.make_signature(z, priv, k)
        height = 58_000_000 + 997 * i
        want_addr = _priv_addr(priv)

        # (a1) 完整 ECDSA 恢复 —— 编译器影子执行（同一 AUTH 宏程序）
        comp = Compiler()
        comp.compile_ecrecover(z, r, s, v, pow(r, -1, N))
        assert not comp.fail, f"[{i}] 影子 fail 置位"
        pub = comp.qx.to_bytes(32, "big") + comp.qy.to_bytes(32, "big")
        assert engine.keccak256(pub)[12:] == want_addr, f"[{i}] 恢复地址不符"

        # 门级全流程（每次掷骰前重置全部零件状态）
        sim.reset()
        d.ticks = 0
        d.shadow = [0] * 16

        # (a2) 门级结构校验 + MUL 锚点
        fail = d.auth_structural(r, s, anchor=True)
        assert not fail, f"[{i}] 结构校验 fail（有效签名不应 fail）"

        # (b) 哈希种子
        msg = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        seed = d.hash_seed(msg)
        want_seed = int.from_bytes(engine.keccak256(msg), "big")
        assert seed == want_seed, f"[{i}] 种子不符"

        # (c) 高度混合 + mod 6 + 1
        summed = d.mix_height_words(seed, height)
        want_sum = (seed + height) & MASK256
        assert summed == want_sum, f"[{i}] 高度混合不符"
        dice = d.stream_dice(summed)
        want_dice = want_sum % 6 + 1
        assert dice == want_dice, f"[{i}] 点数 {dice} != {want_dice}"
        assert 1 <= dice <= 6

        passed += 1
        print(f"✓ [{i}] addr=0x{want_addr.hex()[:8]}… "
              f"seed=0x{seed:064x}"[:52] + "… "
              f"h={height:,} → dice={dice}（{time.time()-tv0:.1f}s）")
    assert passed == end - start
    print(f"✓ 有效签名全流程对拍 {passed} 组通过（区间 [{start},{end})，"
          f"耗时 {time.time()-t0:.0f}s）")


# ---------------------------------------------------------------------------
# 3. 边界测试
# ---------------------------------------------------------------------------

def _expect_fail(r, s):
    return (r == 0) or (s == 0) or (r >= N) or (s >= N)


def test_boundary():
    _, sim = make_shell()
    d = FairDiceDriver(sim, N)

    # --- 无效签名（结构层）→ fail 置位（门级，经真实 ecrecover_ctrl） ---
    cases = [
        ("r=0",          0,         12345),
        ("s=0",          12345,     0),
        ("空输入 r=s=0",  0,         0),
        ("r≥n",          N,         12345),
        ("s≥n（极大数）", 777,       MASK256),
        ("r,s 均超 n",   N + 5,     N + 7),
    ]
    for tag, r, s in cases:
        sim.reset()
        d.ticks = 0
        d.shadow = [0] * 16
        fail = d.auth_structural(r, s, anchor=False)
        assert fail == _expect_fail(r, s), f"{tag}: fail={fail}"
        assert fail, f"{tag}: 无效签名未检出"
        print(f"✓ 边界 {tag:<12} → fail=1（检出）")

    # --- 极大合法值 r=s=n−1 → 全流程正常 ---
    sim.reset()
    d.ticks = 0
    d.shadow = [0] * 16
    r = s = N - 1
    height = 58_123_456
    fail = d.auth_structural(r, s, anchor=True)   # (n−1)² ≡ 1 (mod n) 锚点
    assert not fail, "极大合法值不应 fail"
    msg = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    seed = d.hash_seed(msg)
    assert seed == int.from_bytes(engine.keccak256(msg), "big")
    summed = d.mix_height_words(seed, height)
    assert summed == (seed + height) & MASK256
    dice = d.stream_dice(summed)
    assert dice == summed % 6 + 1 and 1 <= dice <= 6
    print(f"✓ 边界 极大合法值 r=s=n−1 → 全流程正常，dice={dice}")

    # --- 极大数值签名 r‖s=0xFF×64 → 正常取模（跳过 AUTH，纯哈希/混合/取模） ---
    sim.reset()
    d.ticks = 0
    msg = b"\xff" * 64
    seed = d.hash_seed(msg)
    assert seed == int.from_bytes(engine.keccak256(msg), "big")
    summed = d.mix_height_words(seed, 2**256 - 2)   # 高度也取极大（触发回绕）
    assert summed == (seed + 2**256 - 2) & MASK256, "256 位回绕加不符"
    dice = d.stream_dice(summed)
    assert dice == summed % 6 + 1
    print(f"✓ 边界 极大数值 r‖s=0xFF×64 + 回绕高度 → 正常取模，dice={dice}")

    # --- 伪造签名（s 篡改 +1）→ 恢复地址不匹配（应用层检出） ---
    priv, k = 0xC0FFEE, 0xDEADBEEF
    z = int.from_bytes(engine.keccak256(b"FairDice forgery test"), "big")
    r, s, v = engine.make_signature(z, priv, k)
    s_bad = (s + 1) % N
    rec = engine.ecrecover(z, r, s_bad, v)
    want_addr = _priv_addr(priv)
    assert rec is None or rec[2] != want_addr, "伪造签名未被检出"
    got = "None（签名方程无解）" if rec is None else f"0x{rec[2].hex()[:8]}…"
    print(f"✓ 边界 伪造签名 s+1 → 恢复地址 {got} ≠ 签名者 0x{want_addr.hex()[:8]}…"
          "（应用层检出）")


# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    # 分片运行：python tests/test_fairdice_core.py vectors 0 5
    if len(sys.argv) >= 2 and sys.argv[1] == "vectors":
        a = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        b = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        test_vectors_10(a, b)
        print(f"VECTORS [{a},{b}) PASSED（耗时 {time.time()-t0:.0f}s）")
        return
    test_structure_and_fingerprints()
    print()
    test_mod6_accumulator()
    print()
    test_vectors_10()
    print()
    test_boundary()
    print()
    print(f"ALL FAIRDICE CORE TESTS PASSED（耗时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    main()
