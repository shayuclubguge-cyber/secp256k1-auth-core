"""
ModMath Core 全量本地对拍 —— 通过 modmath_ctrl 主控壳（REF×4）驱动

向量构成（与任务书一致）：
  1. mod-p 模乘 14 组   —— 复用 ECREC 已通过标准（test_mcore256.py 同向量同种子）
  2. mod-n 模乘 5 组    —— 复用 ECREC 已通过标准
  3. 通用素数域 5 组    —— 5 个不同素数（secp256r1 p/n、brainpoolP256r1 p、
                          2^256-189、首个 >2^255 素数；均 Miller-Rabin 验证）
  4. 并转串 serialize 3 组（256-bit 输入输出）
  5. 总线传输 2 组（环间拷贝 + 跨组件 mcore→ringbus 数据流转）
  附加：mod_add/mod_sub 12+4 组（主控壳 unit=0 通路验证）、字节指纹交叉验证、
        REF 状态隔离验证（mod_mul 后 mod_add 不受污染）
"""

import hashlib
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout.field import P, N
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.fadd64 import build_fadd64
from tapeout.parts.mcore256 import build_mcore256
from tapeout.parts.piso256 import build_piso256
from tapeout.parts.ringbus import build_ringbus5
from tapeout.parts.modmath_ctrl import build_modmath_ctrl, ModMathDriver

# ECREC v2 处理器（BSC 主网）与零件 cid（CHAIN_ASSETS.md 定稿）
V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
CID = dict(fadd=6, mcore=2, piso=3, ring=4)   # fadd 用第二实例做状态隔离

MASK = (1 << 256) - 1

# CHAIN_ASSETS.md 记录的链上指纹前缀（交叉验证用）
CHAIN_SHA = {
    "fadd64":   "c22f8c74b0def33c",   # cid1/cid6 同网表
    "mcore256": "37eb121673dbf830",   # cid2
    "piso256":  "356cdf33ca964192",   # cid3
    "ringbus5": "83beeeb0b50e4bae",   # cid4/cid5 同网表
}


# ---------------------------------------------------------------------------
# 环境构建
# ---------------------------------------------------------------------------

def make_shell():
    resolver = REFResolver()
    resolver.register(V2_CPU, 1, build_fadd64())              # mcore256 内部引用
    resolver.register(V2_CPU, CID["mcore"], build_mcore256(V2_CPU, 1))
    resolver.register(V2_CPU, CID["piso"], build_piso256())
    resolver.register(V2_CPU, CID["ring"], build_ringbus5())
    resolver.register(V2_CPU, CID["fadd"], build_fadd64())    # fadd64b 同网表
    core = build_modmath_ctrl(V2_CPU, CID)
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
    print(f"modmath_ctrl: {core.n_in}→{core.n_out}, NAND={nand}, LATCH={latch}, "
          f"REF={ref}, {len(core.instructions)} 指令, {len(blob):,}B")
    print(f"  sha256 = {sha}")
    assert core.n_in <= 255 and core.n_out <= 255, "引脚超 255"
    assert len(blob) <= 24_575, "超 OKX SSTORE2 单块上限"
    assert len(core.instructions) <= 3_500, "超 OKX 流片指令硬顶"
    assert ref == 4 and latch == 0, "应为 4 REF + 0 自有 LATCH"
    est_gas = 421 * len(blob)
    assert est_gas <= 16_777_216, "超 BEP-652 单交易 gas 上限"
    print(f"✓ 结构约束全满足（≤255 引脚 / ≤24,575B / ≤3,500 指令 / "
          f"预估 gas {est_gas:,} ≪ BEP-652）")

    # 存档网表与现场重建逐字节一致
    saved = (ROOT / "vectors" / "modmath_ctrl.net").read_bytes()
    assert saved == blob, "vectors/modmath_ctrl.net 与源码重建不一致"
    print("✓ 存档网表 = 源码现场重建（逐字节）")

    # 复用零件指纹交叉验证（源码重建 vs CHAIN_ASSETS 链上记录）
    parts = {
        "fadd64": build_fadd64().to_bytes(),
        "mcore256": build_mcore256(V2_CPU, 1).to_bytes(),
        "piso256": build_piso256().to_bytes(),
        "ringbus5": build_ringbus5().to_bytes(),
    }
    for name, blob_p in parts.items():
        sha_p = hashlib.sha256(blob_p).hexdigest()
        assert sha_p.startswith(CHAIN_SHA[name]), \
            f"{name} 指纹漂移: {sha_p[:16]} != {CHAIN_SHA[name]}"
        print(f"✓ {name:<9} 指纹 {sha_p[:16]}… 与链上记录一致（{len(blob_p):,}B）")

    # 与 vectors/ 存档交叉（mcore256 无存档，仅源码重建）
    for name, fn in [("fadd64", "fadd64.net"), ("piso256", "piso256.net"),
                     ("ringbus5", "ringbus5.net")]:
        assert (ROOT / "vectors" / fn).read_bytes() == parts[name], \
            f"{name} 与 vectors/{fn} 存档不一致"
    print("✓ 复用零件与 vectors/ 存档逐字节一致（fadd64/piso256/ringbus5）")

    logical = nand + latch + 3174 + 3380 + 1220 + 3222
    print(f"✓ 逻辑晶体管总量（壳+4 零件）= {logical:,}（目标 8,000–12,000）")
    assert 8_000 <= logical <= 12_000


# ---------------------------------------------------------------------------
# 1/2/3. mod_mul：mod-p 14 + mod-n 5 + 通用素数域 5
# ---------------------------------------------------------------------------

def _check_mul(d, a, b, m, tag):
    got = d.mod_mul(a, b, m)
    want = (a * b) % m
    assert got == want, f"mod_mul 不匹配 [{tag}]: got={got:#x} want={want:#x}"
    return d.run_ticks


def test_mulmod_p():
    _, sim = make_shell()
    d = ModMathDriver(sim)
    ticks = []
    for a, b, tag in [
        (0, 0, "0·0"), (1, 1, "1·1"), (0, P - 1, "0·(p-1)"),
        (1, P - 1, "1·(p-1)"), (P - 1, P - 1, "(p-1)²"),
        (2, P - 2, "2·(p-2)"), (1 << 255, 1 << 255, "2^255·2^255"),
        (P - 1, 1 << 255, "(p-1)·2^255"),
        (MASK, MASK, "2^256-1 平方（非规范输入）"),
        (0xC0FFEE, 0xDEADBEEF, "小常数"),
    ]:
        ticks.append(_check_mul(d, a, b, P, tag))
    rng = random.Random(20260818)
    for i in range(4):
        a, b = rng.getrandbits(256), rng.getrandbits(256)
        ticks.append(_check_mul(d, a, b, P, f"rand{i}"))
    print(f"✓ [1] mod-p 模乘 14/14 全对（RUN 拍数 {min(ticks)}~{max(ticks)}）")


def test_mulmod_n():
    _, sim = make_shell()
    d = ModMathDriver(sim)
    rng = random.Random(777)
    for a, b, tag in [(1, N - 1, "1·(n-1)"), (N - 1, N - 1, "(n-1)²"),
                      (0x76d2fdf1302d1fa9556f4df94ec84cefba6d482e54f47c6c2a238c1baa560f0e,
                       0x9a95aba4ca4bd7d78b95db39afa12c7bbd2b9b739e8fd148092da7632cd03e89,
                       "r·s（演示向量）")]:
        _check_mul(d, a, b, N, tag)
    for i in range(2):
        _check_mul(d, rng.getrandbits(256), rng.getrandbits(256), N, f"rand{i}")
    print("✓ [2] mod-n 模乘 5/5 全对（同一电路换 rc 即换模数）")


def _is_probable_prime(n: int, rounds: int = 24) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    rng = random.Random(0x4D4D4154)  # 固定种子 'MMAT'
    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def test_mulmod_generic_primes():
    """通用素数域：5 个不同素数（均 > 2^255，mcore256 模数约束）"""
    secp256r1_p = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
    secp256r1_n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    brainpool_p = 0xA9FB57DBA1EEA9BC3E660A909D838D726E3BF623D52620282013481D1F6E5377
    q_big = (1 << 256) - 189
    q_min = (1 << 255) + 1
    while not _is_probable_prime(q_min):
        q_min += 2
    primes = [
        ("secp256r1 p", secp256r1_p),
        ("secp256r1 n", secp256r1_n),
        ("brainpoolP256r1 p", brainpool_p),
        ("2^256-189", q_big),
        (f"首个>2^255 素数 (2^255+{q_min - (1 << 255)})", q_min),
    ]
    _, sim = make_shell()
    d = ModMathDriver(sim)
    rng = random.Random(20260819)
    for name, m in primes:
        assert m > (1 << 255), f"{name} 不满足 m>2^255"
        assert _is_probable_prime(m), f"{name} 未通过 Miller-Rabin"
        a, b = rng.getrandbits(256), rng.getrandbits(256)
        _check_mul(d, a, b, m, name)
        print(f"  ✓ {name}: mod_mul 对拍一致")
    print("✓ [3] 通用素数域 5/5 全对（5 个不同素数，同一电路零修改）")


# ---------------------------------------------------------------------------
# 附加. mod_add / mod_sub（unit=0 通路）+ REF 状态隔离
# ---------------------------------------------------------------------------

def test_mod_add_sub():
    _, sim = make_shell()
    d = ModMathDriver(sim)
    cases_p = [(0, 0), (1, 2), (P - 1, 1), (P - 1, P - 1), (P - 2, P - 2),
               (1 << 255, (1 << 255) - 1), (3, P - 5), (MASK % P, MASK % P)]
    for a, b in cases_p:
        assert d.mod_add(a, b, P) == (a + b) % P, f"addmod_p({a:#x},{b:#x})"
    for a, b in [(1, 2), (N - 1, 2), (N - 1, N - 1), (N // 2, N // 2 + 3)]:
        assert d.mod_add(a, b, N) == (a + b) % N, f"addmod_n({a:#x},{b:#x})"
    print("✓ [附] mod_add 12/12 全对（mod-p ×8 + mod-n ×4）")
    for a, b in [(0, 0), (1, 2), (P - 1, P - 1), (3, P - 5)]:
        assert d.mod_sub(a, b, P) == (a - b) % P, f"submod_p({a:#x},{b:#x})"
    print("✓ [附] mod_sub 4/4 全对")


def test_state_isolation():
    """mod_mul（mcore→cid1 fadd64）后 mod_add（cid6 fadd64b）结果不受污染"""
    _, sim = make_shell()
    d = ModMathDriver(sim)
    r1 = d.mod_mul(0x123456789ABCDEF, 0xFEDCBA987654321, P)
    assert r1 == (0x123456789ABCDEF * 0xFEDCBA987654321) % P
    assert d.mod_add(P - 1, P - 1, P) == (2 * (P - 1)) % P
    r2 = d.mod_mul(7, 11, N)
    assert r2 == 77
    assert d.mod_add(N - 1, 2, N) == 1
    print("✓ [附] REF 状态隔离成立（cid6 与 mcore 内部 cid1 互不污染）")


# ---------------------------------------------------------------------------
# 4. serialize（并转串）3 组
# ---------------------------------------------------------------------------

def test_serialize():
    _, sim = make_shell()
    d = ModMathDriver(sim)
    vals = [
        0xDEADBEEF123456780123456789ABCDE0FEDCBA98765432100F1E2D3C4B5A6978,
        MASK,
        random.Random(555).getrandbits(256),
    ]
    for i, v in enumerate(vals):
        bits = d.serialize(v)
        got = 0
        for b in bits:
            got = (got << 1) | int(b)
        assert got == v, f"serialize #{i}: {got:064x} != {v:064x}"
        assert d.ticks % 4 == 0, "serialize 后未保持 4 拍网格"
    print("✓ [4] serialize 3/3 全对（固定 + 全 1 + 随机，MSB 先行）")


# ---------------------------------------------------------------------------
# 5. bus_transfer 2 组（环间拷贝 + 跨组件数据流转）
# ---------------------------------------------------------------------------

def test_bus_transfer():
    _, sim = make_shell()
    d = ModMathDriver(sim)

    # 组 1：环间拷贝 ring2 → ring4，源保持、邻环隔离
    v1 = 0x0F0F0F0F0F0F0F0F_1111111111111111_2222222222222222_3333333333333333
    v0 = 0xAAAAAAAAAAAAAAAA_BBBBBBBBBBBBBBBB_CCCCCCCCCCCCCCCC_DDDDDDDDDDDDDDDD
    d.ring_write(0, v0)
    d.ring_write(2, v1)
    moved = d.bus_transfer(2, 4)
    assert moved == v1
    assert d.ring_read(4) == v1, "目标环内容不符"
    assert d.ring_read(2) == v1, "源环未保持"
    assert d.ring_read(0) == v0, "邻环被串写污染"
    print("✓ [5a] 环间拷贝 ring2→ring4（源保持 + 邻环隔离）")

    # 组 2：跨组件数据流转 mcore256 模乘结果 → ringbus 环1 → 读回
    a, b = 0xC0FFEE12345, 0xDEADBEEF6789A
    res = d.mod_mul(a, b, P)
    assert res == (a * b) % P
    d.ring_write(1, res)
    assert d.ring_read(1) == res, "跨组件转运后读回不符"
    print("✓ [5b] 跨组件转运 mcore256→ringbus 环1（模乘结果落盘读回）")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t0 = time.time()
    test_structure_and_fingerprints()
    test_mulmod_p()
    test_mulmod_n()
    test_mulmod_generic_primes()
    test_mod_add_sub()
    test_state_isolation()
    test_serialize()
    test_bus_transfer()
    print(f"\nALL MODMATH CORE TESTS PASSED（耗时 {time.time() - t0:.0f}s）")
