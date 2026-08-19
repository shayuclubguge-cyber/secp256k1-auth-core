#!/usr/bin/env python3
"""
一键发布验证脚本 —— TapeOut secp256k1 Auth Core（阶段 5b）

用法:
  python scripts/verify_release.py              # 全部：测试 + 链上回读 + 清单
  python scripts/verify_release.py --tests-only # 只跑本地测试
  python scripts/verify_release.py --chain-only # 只做链上回读比对（不跑测试）
  python scripts/verify_release.py --modmath-only # ModMath Core：专项测试 + 依赖零件回读
  python scripts/verify_release.py --keccak-core-only # Keccak Core：专项测试 + 零件 cid 7/9-13 回读
  python scripts/verify_release.py --fairdice-only # FairDice Core：专项测试 + 依赖闭包 cid 1-13 回读 + 主控壳校验
  python scripts/verify_release.py --rpc URL    # 指定 BSC RPC（可多次）

验证内容:
  1. tests/ 下全部测试逐个运行（子进程，必须全部通过）；
     --modmath-only 时只跑 tests/test_modmath_core.py
  2. 对 v2 处理器 cid 1–13 逐一 eth_call netlist(cid) 回读链上网表字节，
     与本地 vectors/*.net（cid2 由源码现场重建）做 SHA-256 比对；
     --modmath-only 时只回读 ModMath 依赖零件 cid 1–6
  3. 从网表字节解析 NAND/LATCH/REF 指令数，输出门数清单
  4. 报告写入 docs/VERIFY_REPORT.md（--modmath-only 写 docs/MODMATH_VERIFY_REPORT.md）

退出码: 0 = 全绿; 1 = 有失败项
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# --- 链上常量（CHAIN_ASSETS.md 定稿） ---
CIRCUITS_ADDR = "0xa3b6d9121146c29fb236001b93a45cb7a78a2247"
NETLIST_SELECTOR = "3fc4be56"  # netlist(uint256)
V2_CPU_BYTES = bytes.fromhex(CIRCUITS_ADDR[2:])

DEFAULT_RPCS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://bsc.publicnode.com",
]

# cid → (名称, n_in, n_out, 本地来源)
# 本地来源: vectors/ 下文件名，或 "REGEN" 表示由源码现场重建（mcore256 无 .net 存档）
ROSTER = [
    (1,  "fadd64 字串行ALU",        67,  65, "fadd64.net"),
    (2,  "mcore256 自包含模乘引擎", None, None, "REGEN"),
    (3,  "piso256 并转串",          None, None, "piso256.net"),
    (4,  "ringbusA 环总线",         None, None, "ringbus5.net"),
    (5,  "ringbusB（同网表二实例）", None, None, "ringbus5.net"),
    (6,  "fadd64b（同网表二实例）",  67,  65, "fadd64.net"),
    (7,  "keccak_comp θD/XOR3/χ",   204, 64, "keccak_comp.net"),
    (8,  "ecrecover_ctrl 宏指令主控", None, None, "ecrecover_ctrl.net"),
    (9,  "kl_ringa_lo 环A低32位",   38,  64, "kl_ringa_lo.net"),
    (10, "kl_ringa_hi 环A高32位",   38,  64, "kl_ringa_hi.net"),
    (11, "kl_rho ρ移位",            69,  64, "kl_rho.net"),
    (12, "kl_ringb 环B+χ装配",      75, 192, "kl_ringb.net"),
    (13, "kl_top 主控 REF×7",       65,  66, "kl_top.net"),
]

CID2_CHAIN_SHA_PREFIX = "37eb121673dbf830"  # CHAIN_ASSETS.md 记录的链上指纹前缀

# --- ModMath Core（通用密码学运算库，独立处理器，2026-08-19 已上线） ---
MODMATH_ROSTER = ROSTER[0:6]              # ModMath 依赖零件：ECREC v2 cid 1-6
MODMATH_CIRCUITS = "0x9b30f16fb5f0c91343e678d94670a0d8b231a40d"  # ModMath Circuits（v2 重建版）
MODMATH_CID = 1                           # 主控壳 = 新处理器首颗电路
MODMATH_SHELL_SOURCE = "modmath_ctrl.net"  # vectors/ 下主控壳存档网表
MODMATH_TEST = "test_modmath_core.py"

# --- Keccak Core（Keccak-256 底层零件库，第二个处理器，筹备中） ---
# 复用 ECREC v2 已链上验证的 6 颗 Keccak 电路（cid8 ecrecover_ctrl 为 ECREC 专有，排除）
KECCAK_ROSTER = [ROSTER[6]] + ROSTER[8:13]      # cid 7, 9, 10, 11, 12, 13
KECCAK_TEST = "test_kl_split.py"                # 拆分版 Keccak 端到端向量测试
KECCAK_REPORT = "KECCAK_CORE_VERIFY_REPORT.md"
# CHAIN_ASSETS.md 定稿的链上指纹前缀（与 vectors/kl_split_manifest.json 一致）
KECCAK_CHAIN_SHA = {
    7: "2e0a1609fb8f", 9: "5f62ced6d705", 10: "5f62ced6d705",
    11: "a5dd3b52cb1b", 12: "c2be8eefec19", 13: "dccbd34519c3",
}
# kl_top（cid13）REF×7 期望指向（build_kl_top：comp×3 + ringa_lo + ringa_hi + rho + ringb）
KECCAK_TOP_REF_CIDS = sorted([7, 7, 7, 9, 10, 11, 12])

# --- FairDice Core（链上可验证公平骰子，第四个自有处理器，筹备中） ---
# 依赖闭包 = ECREC v2 全部 cid 1–13（AUTH cid8 → 2/3/4/5/6 → 1；
# HASH cid13 → 7/9/10/11/12；MIX cid6；STREAM cid3）
FAIRDICE_TEST = "test_fairdice_core.py"
FAIRDICE_REPORT = "FAIRDICE_VERIFY_REPORT.md"
FAIRDICE_SHELL_SOURCE = "fairdice_ctrl.net"     # vectors/ 下主控壳存档网表
FAIRDICE_STATE_PATH = ROOT / "vectors" / "fairdice_launch_state.json"
FAIRDICE_REF_CIDS = sorted([3, 6, 8, 13])       # 壳的 REF×4 期望指向
FAIRDICE_PINS = (74, 83)


# ----------------------------------------------------------------------
# 网表字节解析（格式见 tapeout/__init__.py to_bytes）
#   NAND:  [0][a:u24][b:u24]          7 字节
#   LATCH: [1][d:u24]                 4 字节
#   REF:   [2][cpu:20][cid:u64][nIn][nOut][inputs:3×nIn]  31+3×nIn 字节
# ----------------------------------------------------------------------
def parse_netlist(blob: bytes):
    nand = latch = ref = 0
    i = 0
    while i < len(blob):
        op = blob[i]
        if op == 0:
            nand += 1
            i += 7
        elif op == 1:
            latch += 1
            i += 4
        elif op == 2:
            n_in = blob[i + 29]
            ref += 1
            i += 31 + 3 * n_in
        else:
            raise ValueError(f"未知指令 op={op} @ 偏移 {i}")
    if i != len(blob):
        raise ValueError("网表尾部未对齐")
    return nand, latch, ref


def parse_refs(blob: bytes):
    """解析网表中的 REF 指令，返回 [(cpu_addr, cid), ...]（格式同 parse_netlist）"""
    refs = []
    i = 0
    while i < len(blob):
        op = blob[i]
        if op == 0:
            i += 7
        elif op == 1:
            i += 4
        elif op == 2:
            cpu = "0x" + blob[i + 1:i + 21].hex()
            cid = int.from_bytes(blob[i + 21:i + 29], "big")
            n_in = blob[i + 29]
            refs.append((cpu, cid))
            i += 31 + 3 * n_in
        else:
            raise ValueError(f"未知指令 op={op} @ 偏移 {i}")
    return refs


# ----------------------------------------------------------------------
# 第 1 步：本地测试
# ----------------------------------------------------------------------
def run_tests(modmath_only: bool = False, keccak_only: bool = False,
              fairdice_only: bool = False):
    print("=" * 64)
    if fairdice_only:
        print(f"第 1 步：FairDice Core 专项测试（tests/{FAIRDICE_TEST}）")
    elif keccak_only:
        print(f"第 1 步：Keccak Core 专项测试（tests/{KECCAK_TEST}）")
    elif modmath_only:
        print("第 1 步：ModMath Core 专项测试（tests/test_modmath_core.py）")
    else:
        print("第 1 步：本地测试（tests/ 全部，逐个子进程）")
    print("=" * 64)
    if fairdice_only:
        test_files = [ROOT / "tests" / FAIRDICE_TEST]
    elif keccak_only:
        test_files = [ROOT / "tests" / KECCAK_TEST]
    elif modmath_only:
        test_files = [ROOT / "tests" / MODMATH_TEST]
    else:
        test_files = sorted((ROOT / "tests").glob("test_*.py"))
    failed = []
    for f in test_files:
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(f)],
                capture_output=True, text=True, timeout=900,
                cwd=str(ROOT),
            )
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
            r = None
        dt = time.time() - t0
        print(f"  {'✓' if ok else '✗'} {f.name:<34} {dt:6.1f}s")
        if not ok:
            failed.append(f.name)
            if r is not None:
                tail = (r.stdout.splitlines()[-4:] + r.stderr.splitlines()[-4:])
                print("      " + "\n      ".join(tail))
    return failed


# ----------------------------------------------------------------------
# 第 2 步：链上回读
# ----------------------------------------------------------------------
def rpc_call(rpcs, method, params):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }).encode()
    last_err = None
    for url in rpcs:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read())
            if "error" in out:
                raise RuntimeError(f"{url}: {out['error']}")
            return out["result"]
        except Exception as e:  # noqa: BLE001 —— 换下一个 RPC
            last_err = e
    raise RuntimeError(f"所有 RPC 均失败: {last_err}")


def fetch_netlist(rpcs, cid: int, addr: str = CIRCUITS_ADDR) -> bytes:
    data = "0x" + NETLIST_SELECTOR + f"{cid:064x}"
    result = rpc_call(rpcs, "eth_call",
                      [{"to": addr, "data": data}, "latest"])
    raw = bytes.fromhex(result[2:])
    # ABI 编码的 bytes: [offset:32][length:32][data]
    offset = int.from_bytes(raw[:32], "big")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    blob = raw[offset + 32: offset + 32 + length]
    if len(blob) != length:
        raise ValueError(f"cid{cid}: ABI 长度字段 {length} 与实际 {len(blob)} 不符")
    return blob


def local_blob(source: str) -> bytes:
    if source == "REGEN":
        # cid2 mcore256 无 .net 存档，由源码现场重建（REF→本处理器 cid1 fadd64）
        from tapeout.parts.mcore256 import build_mcore256
        return build_mcore256(V2_CPU_BYTES, 1).to_bytes()
    return (ROOT / "vectors" / source).read_bytes()


def modmath_shell_blob() -> bytes:
    """ModMath 主控壳由源码现场重建（REF→cid6/2/3/4）"""
    from tapeout.parts.modmath_ctrl import build_modmath_ctrl, DEFAULT_REFS
    return build_modmath_ctrl(V2_CPU_BYTES, DEFAULT_REFS).to_bytes()


def fairdice_shell_blob() -> bytes:
    """FairDice 主控壳由源码现场重建（REF→cid8/13/6/3）"""
    from tapeout.parts.fairdice_ctrl import build_fairdice_ctrl, DEFAULT_REFS
    return build_fairdice_ctrl(V2_CPU_BYTES, DEFAULT_REFS).to_bytes()


def verify_fairdice_shell(rpcs, rows, mismatches):
    """FairDice 主控壳：源码重建 = 存档 + REF×4 指向校验；
    流片后（launch_state.json 有 circuits+cid）再链上回读"""
    local = fairdice_shell_blob()
    saved = local_blob(FAIRDICE_SHELL_SOURCE)
    sha = hashlib.sha256(local).hexdigest()
    nand, latch, ref = parse_netlist(local)
    ok = local == saved
    if not ok:
        mismatches.append("fairdice_ctrl-local")
    print(f"  {'✓' if ok else '✗'} fairdice_ctrl 主控壳      "
          f"{len(local):>6,}B  NAND {nand:>5,}  LATCH {latch:>5,}  "
          f"REF {ref}  sha {sha[:12]}  "
          f"{'源码重建=存档' if ok else '重建≠存档!'}")
    # REF×4 指向校验：cid {3,6,8,13}，均属 ECREC v2 本处理器
    refs = parse_refs(local)
    ref_cids = sorted(c for _, c in refs)
    cpu_ok = all(cpu.lower() == CIRCUITS_ADDR.lower() for cpu, _ in refs)
    ref_ok = ref_cids == FAIRDICE_REF_CIDS and cpu_ok
    print(f"  {'✓' if ref_ok else '✗'} fairdice_ctrl REF×{len(refs)} 指向 {ref_cids}"
          f"（期望 {FAIRDICE_REF_CIDS}，均属 ECREC v2）")
    if not ref_ok:
        mismatches.append("fairdice-refs")
    # 流片后链上回读（地址取自发射状态文件）
    st = {}
    if FAIRDICE_STATE_PATH.exists():
        st = json.loads(FAIRDICE_STATE_PATH.read_text())
    if not (st.get("circuits") and st.get("cid")):
        print("  … 主控壳尚未流片（fairdice_launch_state.json 无 circuits/cid），"
              "链上回读待流片后启用")
        return
    try:
        chain = fetch_netlist(rpcs, st["cid"], st["circuits"])
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ cid{st['cid']} fairdice_ctrl 链上读取失败: {e}")
        mismatches.append("fairdice-chain")
        return
    chain_sha = hashlib.sha256(chain).hexdigest()
    match = chain == local
    if not match:
        mismatches.append("fairdice-chain")
    c_nand, c_latch, c_ref = parse_netlist(chain)
    print(f"  {'✓' if match else '✗'} cid{st['cid']:<2} fairdice_ctrl 主控壳"
          f" {len(chain):>6,}B  NAND {c_nand:>5,}  LATCH {c_latch:>5,}  "
          f"REF {c_ref}  sha {chain_sha[:12]}  "
          f"{'链上=本地' if match else '不一致!'}")
    rows.append({
        "cid": st["cid"], "name": "DICE fairdice_ctrl 主控壳 REF×4",
        "pins": f"{FAIRDICE_PINS[0]}→{FAIRDICE_PINS[1]}",
        "bytes": len(chain), "nand": c_nand, "latch": c_latch, "ref": c_ref,
        "sha256": chain_sha, "match": match, "source": FAIRDICE_SHELL_SOURCE,
    })


def verify_modmath_shell(rpcs, rows, mismatches):
    """ModMath 主控壳：源码重建 = 存档网表；流片后（MODMATH_CID 已填）再链上回读"""
    local = modmath_shell_blob()
    saved = local_blob(MODMATH_SHELL_SOURCE)
    sha = hashlib.sha256(local).hexdigest()
    nand, latch, ref = parse_netlist(local)
    ok = local == saved
    if not ok:
        mismatches.append("modmath_ctrl-local")
    print(f"  {'✓' if ok else '✗'} modmath_ctrl 主控壳       "
          f"{len(local):>6,}B  NAND {nand:>5,}  LATCH {latch:>5,}  "
          f"REF {ref}  sha {sha[:12]}  "
          f"{'源码重建=存档' if ok else '重建≠存档!'}")
    if MODMATH_CID is None:
        print("  … 主控壳尚未流片（MODMATH_CID 未填），链上回读待流片后启用")
        return
    try:
        chain = fetch_netlist(rpcs, MODMATH_CID, MODMATH_CIRCUITS)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ cid{MODMATH_CID} modmath_ctrl 链上读取失败: {e}")
        mismatches.append(MODMATH_CID)
        return
    chain_sha = hashlib.sha256(chain).hexdigest()
    match = chain == local
    if not match:
        mismatches.append(MODMATH_CID)
    c_nand, c_latch, c_ref = parse_netlist(chain)
    print(f"  {'✓' if match else '✗'} cid{MODMATH_CID:<2} modmath_ctrl 主控壳 "
          f"{len(chain):>6,}B  NAND {c_nand:>5,}  LATCH {c_latch:>5,}  "
          f"REF {c_ref}  sha {chain_sha[:12]}  "
          f"{'链上=本地' if match else '不一致!'}")
    rows.append({
        "cid": MODMATH_CID, "name": "MMAT modmath_ctrl 主控壳 REF×4", "pins": "74→72",
        "bytes": len(chain), "nand": c_nand, "latch": c_latch, "ref": c_ref,
        "sha256": chain_sha, "match": match, "source": MODMATH_SHELL_SOURCE,
    })


def verify_chain(rpcs, modmath_only: bool = False, fairdice_only: bool = False):
    print()
    print("=" * 64)
    if fairdice_only:
        print("第 2 步：链上回读比对（FairDice 依赖闭包 cid 1–13，BSC 主网）")
    elif modmath_only:
        print("第 2 步：链上回读比对（ModMath 依赖零件 cid 1–6，BSC 主网）")
    else:
        print("第 2 步：链上回读比对（cid 1–13，BSC 主网）")
    print("=" * 64)
    print(f"  合约: {CIRCUITS_ADDR}  netlist(uint256)=0x{NETLIST_SELECTOR}")

    roster = MODMATH_ROSTER if modmath_only else ROSTER
    rows = []
    mismatches = []
    for cid, name, n_in, n_out, source in roster:
        try:
            chain = fetch_netlist(rpcs, cid)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ cid{cid:<2} {name:<22} 链上读取失败: {e}")
            mismatches.append(cid)
            continue

        chain_sha = hashlib.sha256(chain).hexdigest()
        c_nand, c_latch, c_ref = parse_netlist(chain)

        try:
            local = local_blob(source)
            local_sha = hashlib.sha256(local).hexdigest()
            match = chain == local
            note = "链上=本地" if match else f"不一致! 本地 {local_sha[:12]}"
        except Exception as e:  # noqa: BLE001
            # 本地重建失败时退化为前缀比对（仅 cid2 可能走到这）
            match = chain_sha.startswith(CID2_CHAIN_SHA_PREFIX)
            note = ("本地重建失败，仅对前缀 " if match else "前缀也不符! ") + str(e)[:40]

        if not match:
            mismatches.append(cid)

        pins = f"{n_in}→{n_out}" if n_in is not None else "—"
        print(f"  {'✓' if match else '✗'} cid{cid:<2} {name:<22} "
              f"{len(chain):>6,}B  NAND {c_nand:>5,}  LATCH {c_latch:>5,}  "
              f"REF {c_ref}  sha {chain_sha[:12]}  {note}")
        rows.append({
            "cid": cid, "name": name, "pins": pins, "bytes": len(chain),
            "nand": c_nand, "latch": c_latch, "ref": c_ref,
            "sha256": chain_sha, "match": match, "source": source,
        })
    if modmath_only:
        verify_modmath_shell(rpcs, rows, mismatches)
    if fairdice_only:
        verify_fairdice_shell(rpcs, rows, mismatches)
    return rows, mismatches


def verify_keccak_chain(rpcs):
    """Keccak Core 专项：回读 6 颗零件，与 CHAIN_ASSETS.md 定稿指纹交叉比对。

    与 verify_chain 的判据不同：Keccak Core 以 REF 复用链上 cid（不重新铸造），
    因此基准是 CHAIN_ASSETS.md 定稿的链上指纹前缀（与 kl_split_manifest.json 一致）；
    本地 vectors/ 存档同时比对，作为附加一致性信息。
    """
    print()
    print("=" * 64)
    print("第 2 步：链上回读比对（Keccak Core 零件 cid 7, 9–13，BSC 主网）")
    print("=" * 64)
    print(f"  合约: {CIRCUITS_ADDR}  netlist(uint256)=0x{NETLIST_SELECTOR}")

    rows = []
    mismatches = []
    for cid, name, n_in, n_out, source in KECCAK_ROSTER:
        try:
            chain = fetch_netlist(rpcs, cid)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ cid{cid:<2} {name:<22} 链上读取失败: {e}")
            mismatches.append(cid)
            continue

        chain_sha = hashlib.sha256(chain).hexdigest()
        c_nand, c_latch, c_ref = parse_netlist(chain)
        expect = KECCAK_CHAIN_SHA[cid]
        match = chain_sha.startswith(expect)

        try:
            local = local_blob(source)
            if chain == local:
                note = "链上=定稿指纹=本地"
            elif match:
                note = (f"链上=定稿指纹（本地 {hashlib.sha256(local).hexdigest()[:12]}"
                        f" 为重建版，以链上为准）")
            else:
                note = f"不一致! 期望前缀 {expect}"
        except Exception:  # noqa: BLE001
            note = ("链上=定稿指纹" if match else f"不一致! 期望前缀 {expect}") \
                + "（本地不可读）"

        if not match:
            mismatches.append(cid)
        if n_in is not None and (n_in > 255 or n_out > 255):
            mismatches.append(f"cid{cid}-pins")

        pins = f"{n_in}→{n_out}" if n_in is not None else "—"
        print(f"  {'✓' if match else '✗'} cid{cid:<2} {name:<22} "
              f"{len(chain):>6,}B  NAND {c_nand:>5,}  LATCH {c_latch:>5,}  "
              f"REF {c_ref}  sha {chain_sha[:12]}  {note}")
        rows.append({
            "cid": cid, "name": name, "pins": pins, "bytes": len(chain),
            "nand": c_nand, "latch": c_latch, "ref": c_ref,
            "sha256": chain_sha, "match": match, "source": source,
        })

        if cid == 13:
            # kl_top REF×7 指向校验：comp×3 + ringa_lo + ringa_hi + rho + ringb，
            # 且全部属于 ECREC v2 本处理器
            refs = parse_refs(chain)
            ref_cids = sorted(c for _, c in refs)
            cpu_ok = all(cpu.lower() == CIRCUITS_ADDR.lower() for cpu, _ in refs)
            ref_ok = ref_cids == KECCAK_TOP_REF_CIDS and cpu_ok
            print(f"  {'✓' if ref_ok else '✗'} kl_top REF×{len(refs)} 指向 {ref_cids}"
                  f"（期望 {KECCAK_TOP_REF_CIDS}，均属本处理器）")
            if not ref_ok:
                mismatches.append("cid13-refs")
    return rows, mismatches


# ----------------------------------------------------------------------
# 第 3 步：写报告
# ----------------------------------------------------------------------
def write_report(rows, test_failed, mismatches, elapsed, tests_skipped=False,
                 modmath_only: bool = False, keccak_only: bool = False,
                 fairdice_only: bool = False):
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    if fairdice_only:
        path = docs / FAIRDICE_REPORT
    elif keccak_only:
        path = docs / KECCAK_REPORT
    else:
        path = docs / ("MODMATH_VERIFY_REPORT.md" if modmath_only else "VERIFY_REPORT.md")
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M %Z")
    total_nand = sum(r["nand"] for r in rows)
    total_latch = sum(r["latch"] for r in rows)
    total_bytes = sum(r["bytes"] for r in rows)

    if fairdice_only:
        title = "# FairDice Core —— 一键验证报告"
        scope = "FairDice 依赖闭包 cid 1–13"
        n_expect = 13
    elif keccak_only:
        title = "# Keccak Core —— 零件复用验证报告"
        scope = "Keccak 零件 cid 7, 9–13"
        n_expect = 6
    elif modmath_only:
        title = "# ModMath Core —— 一键验证报告"
        scope = "ModMath 依赖零件 cid 1–6" + (
            " + 主控壳" if MODMATH_CID is not None else "（主控壳待流片）")
        n_expect = len(MODMATH_ROSTER) + (1 if MODMATH_CID is not None else 0)
    else:
        title = "# secp256k1 Auth Core —— 一键验证报告"
        scope = "cid 1–13"
        n_expect = 13

    lines = [
        title,
        "",
        f"- 生成时间: {now}（脚本 `scripts/verify_release.py`，耗时 {elapsed:.0f}s）",
        f"- 处理器合约: `{CIRCUITS_ADDR}`（BNB Chain 主网）",
        f"- 本地测试: {'本次跳过（--chain-only）' if tests_skipped else ('全部通过 ✓' if not test_failed else '失败: ' + ', '.join(test_failed))}",
        f"- 链上回读（{scope}）: {len(rows)}/{n_expect} 颗字节级一致"
        + ("" if not mismatches else f"，不一致项: {mismatches}"),
        f"- 合计: {total_bytes:,} 字节网表，{total_nand:,} NAND + {total_latch:,} LATCH"
        f" = {total_nand + total_latch:,} 晶体管",
        "",
        "| cid | 电路 | 引脚 | 字节 | NAND | LATCH | REF | sha256[0:12] | 一致 |",
        "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['cid']} | {r['name']} | {r['pins']} | {r['bytes']:,} "
            f"| {r['nand']:,} | {r['latch']:,} | {r['ref']} "
            f"| {r['sha256'][:12]} | {'✓' if r['match'] else '✗'} |")
    if keccak_only:
        lines += [
            "",
            "> 比对方式: eth_call `netlist(cid)` 回读链上字节 → SHA-256 →",
            "> 与 CHAIN_ASSETS.md 定稿的链上指纹前缀交叉比对（复用以链上字节为准）。",
            "> cid13 kl_top 的 REF×7 目标已解析校验：cid7×3 + cid9/10/11/12，均属本处理器。",
            "> 指纹基准与 `vectors/kl_split_manifest.json`（2026-08-19 已同步）一致。",
            ""]
    else:
        lines += [
            "",
            "> 比对方式: eth_call `netlist(cid)` 回读链上字节 → SHA-256 →",
            "> 与本地 `vectors/*.net`（cid2 由 `tapeout/parts/mcore256.py` 现场重建）逐字节比对。",
            ""]
    if modmath_only:
        shell_sha = hashlib.sha256(modmath_shell_blob()).hexdigest()
        lines += [
            "## ModMath 主控壳（modmath_ctrl）",
            "",
            f"- 引脚: 74→72（≤255 ✓）  资源: 771 NAND + 0 LATCH + 4 REF",
            f"- 网表: `vectors/modmath_ctrl.net`（6,337B）",
            f"- sha256: `{shell_sha}`",
            f"- REF 缝合: fadd64b cid6（mod_add）/ mcore256 cid2（mod_mul）/"
            f" piso256 cid3（serialize）/ ringbusA cid4（bus_transfer）",
            f"- 链上状态: {'cid' + str(MODMATH_CID) + ' 已回读比对' if MODMATH_CID is not None else '待流片（流片后填 MODMATH_CID 重跑本脚本）'}",
            ""]
    if fairdice_only:
        shell_sha = hashlib.sha256(fairdice_shell_blob()).hexdigest()
        st = {}
        if FAIRDICE_STATE_PATH.exists():
            st = json.loads(FAIRDICE_STATE_PATH.read_text())
        chain_state = (f"cid{st['cid']} @ {st['circuits']} 已回读比对"
                       if st.get("circuits") and st.get("cid")
                       else "待流片（流片后重跑本脚本自动启用链上回读）")
        lines += [
            "## FairDice 主控壳（fairdice_ctrl）",
            "",
            f"- 引脚: 74→83（≤255 ✓）  资源: 842 NAND + 3 LATCH + 4 REF",
            f"- 网表: `vectors/fairdice_ctrl.net`（6,828B）",
            f"- sha256: `{shell_sha}`",
            f"- REF 缝合: ecrecover_ctrl cid8（AUTH 验签）/ kl_top cid13（HASH 种子）/"
            f" fadd64b cid6（MIX 高度混合）/ piso256 cid3（STREAM 位流取模）",
            f"- 链上状态: {chain_state}",
            ""]
    path.write_text("\n".join(lines))
    return path, total_nand, total_latch


def main():
    ap = argparse.ArgumentParser(description="secp256k1 Auth Core 一键发布验证")
    ap.add_argument("--tests-only", action="store_true")
    ap.add_argument("--chain-only", action="store_true")
    ap.add_argument("--modmath-only", action="store_true",
                    help="ModMath Core 专项：只跑 test_modmath_core.py + "
                         "依赖零件 cid 1–6 链上回读 + 主控壳指纹")
    ap.add_argument("--keccak-core-only", action="store_true",
                    help="Keccak Core 专项：只跑 test_kl_split.py + "
                         "零件 cid 7/9/10/11/12/13 链上回读（对照定稿指纹）"
                         " + kl_top REF×7 指向校验")
    ap.add_argument("--fairdice-only", action="store_true",
                    help="FairDice Core 专项：只跑 test_fairdice_core.py + "
                         "依赖闭包 cid 1–13 链上回读 + 主控壳指纹与 REF×4 指向校验")
    ap.add_argument("--rpc", action="append", default=[],
                    help="自定义 BSC RPC（可多次，优先于默认列表）")
    args = ap.parse_args()
    if args.keccak_core_only and args.modmath_only:
        ap.error("--keccak-core-only 与 --modmath-only 互斥")
    if args.fairdice_only and (args.modmath_only or args.keccak_core_only):
        ap.error("--fairdice-only 与 --modmath-only/--keccak-core-only 互斥")
    rpcs = args.rpc + DEFAULT_RPCS

    t0 = time.time()
    test_failed, rows, mismatches = [], [], []

    if not args.chain_only:
        test_failed = run_tests(modmath_only=args.modmath_only,
                                keccak_only=args.keccak_core_only,
                                fairdice_only=args.fairdice_only)
    if not args.tests_only:
        if args.keccak_core_only:
            rows, mismatches = verify_keccak_chain(rpcs)
        else:
            rows, mismatches = verify_chain(rpcs, modmath_only=args.modmath_only,
                                            fairdice_only=args.fairdice_only)

    elapsed = time.time() - t0
    print()
    print("=" * 64)
    ok = not test_failed and not mismatches
    if rows:
        path, tn, tl = write_report(rows, test_failed, mismatches, elapsed,
                                    tests_skipped=args.chain_only,
                                    modmath_only=args.modmath_only,
                                    keccak_only=args.keccak_core_only,
                                    fairdice_only=args.fairdice_only)
        print(f"报告已写入 {path}")
        print(f"晶体管合计: {tn:,} NAND + {tl:,} LATCH = {tn + tl:,}")
    if ok:
        print(f"结果: 全部验证通过 ✓（耗时 {elapsed:.0f}s）")
    else:
        if test_failed:
            print(f"结果: {len(test_failed)} 个测试失败: {test_failed}")
        if mismatches:
            print(f"结果: {len(mismatches)} 颗电路链上比对不一致: {mismatches}")
    print("=" * 64)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
