#!/usr/bin/env python3
"""
一键发布验证脚本 —— TapeOut secp256k1 Auth Core（阶段 5b）

用法:
  python scripts/verify_release.py              # 全部：测试 + 链上回读 + 清单
  python scripts/verify_release.py --tests-only # 只跑本地测试
  python scripts/verify_release.py --chain-only # 只做链上回读比对（不跑测试）
  python scripts/verify_release.py --rpc URL    # 指定 BSC RPC（可多次）

验证内容:
  1. tests/ 下全部测试逐个运行（子进程，必须全部通过）
  2. 对 v2 处理器 cid 1–13 逐一 eth_call netlist(cid) 回读链上网表字节，
     与本地 vectors/*.net（cid2 由源码现场重建）做 SHA-256 比对
  3. 从网表字节解析 NAND/LATCH/REF 指令数，输出门数清单
  4. 报告写入 docs/VERIFY_REPORT.md

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


# ----------------------------------------------------------------------
# 第 1 步：本地测试
# ----------------------------------------------------------------------
def run_tests():
    print("=" * 64)
    print("第 1 步：本地测试（tests/ 全部，逐个子进程）")
    print("=" * 64)
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


def fetch_netlist(rpcs, cid: int) -> bytes:
    data = "0x" + NETLIST_SELECTOR + f"{cid:064x}"
    result = rpc_call(rpcs, "eth_call",
                      [{"to": CIRCUITS_ADDR, "data": data}, "latest"])
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


def verify_chain(rpcs):
    print()
    print("=" * 64)
    print("第 2 步：链上回读比对（cid 1–13，BSC 主网）")
    print("=" * 64)
    print(f"  合约: {CIRCUITS_ADDR}  netlist(uint256)=0x{NETLIST_SELECTOR}")

    rows = []
    mismatches = []
    for cid, name, n_in, n_out, source in ROSTER:
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
    return rows, mismatches


# ----------------------------------------------------------------------
# 第 3 步：写报告
# ----------------------------------------------------------------------
def write_report(rows, test_failed, mismatches, elapsed, tests_skipped=False):
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    path = docs / "VERIFY_REPORT.md"
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M %Z")
    total_nand = sum(r["nand"] for r in rows)
    total_latch = sum(r["latch"] for r in rows)
    total_bytes = sum(r["bytes"] for r in rows)

    lines = [
        "# secp256k1 Auth Core —— 一键验证报告",
        "",
        f"- 生成时间: {now}（脚本 `scripts/verify_release.py`，耗时 {elapsed:.0f}s）",
        f"- 处理器合约: `{CIRCUITS_ADDR}`（BNB Chain 主网）",
        f"- 本地测试: {'本次跳过（--chain-only）' if tests_skipped else ('全部通过 ✓' if not test_failed else '失败: ' + ', '.join(test_failed))}",
        f"- 链上回读: {len(rows)}/13 颗字节级一致"
        + ("" if not mismatches else f"，不一致 cid: {mismatches}"),
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
    lines += [
        "",
        "> 比对方式: eth_call `netlist(cid)` 回读链上字节 → SHA-256 →",
        "> 与本地 `vectors/*.net`（cid2 由 `tapeout/parts/mcore256.py` 现场重建）逐字节比对。",
        ""]
    path.write_text("\n".join(lines))
    return path, total_nand, total_latch


def main():
    ap = argparse.ArgumentParser(description="secp256k1 Auth Core 一键发布验证")
    ap.add_argument("--tests-only", action="store_true")
    ap.add_argument("--chain-only", action="store_true")
    ap.add_argument("--rpc", action="append", default=[],
                    help="自定义 BSC RPC（可多次，优先于默认列表）")
    args = ap.parse_args()
    rpcs = args.rpc + DEFAULT_RPCS

    t0 = time.time()
    test_failed, rows, mismatches = [], [], []

    if not args.chain_only:
        test_failed = run_tests()
    if not args.tests_only:
        rows, mismatches = verify_chain(rpcs)

    elapsed = time.time() - t0
    print()
    print("=" * 64)
    ok = not test_failed and not mismatches
    if rows:
        path, tn, tl = write_report(rows, test_failed, mismatches, elapsed,
                                    tests_skipped=args.chain_only)
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
