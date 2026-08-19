#!/usr/bin/env python3
"""
独立链上审计脚本 —— 模拟第三方审计员验证 ECREC 项目声明

原则（按审计任务书）：
- 只用链上 RPC 数据，不信任项目方素材（声明值作为「待证伪对象」输入）
- 自带纯 Python Keccak-256（自校验：空串/abc/name() selector）
- 每项输出: PASS / FAIL / UNVERIFIABLE + 原始证据
- 证据存 docs/audit_evidence.json
"""

import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RPCS = [
    "https://rpc-bsc.48.club",
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
]
LOGS_RPC = "https://rpc-bsc.48.club"  # 支持 5000 块历史 eth_getLogs

CIRCUITS = "0xa3b6d9121146c29fb236001b93a45cb7a78a2247"
TRANSISTORS = "0x40666990f6740a41e51391cec6559a2158177bf2"
FACTORY = "0x68224F668083c29e9800Be2a646d42d18cedF7e2"
WITNESS = "0xA2dEFD5DE77698eA3896dF9972CD35a9b6FF7DCf"
CREATION_TX = "0x8c2b4e8f7448d620bec9e3cd8fc4970bab4e3068c435fb4463b39240ec4afcfb"
OKX_WALLET = "0x53bdd02b35022f5f2693c252d86d9093ccf95d0b"
MM_WALLET = "0x6EB36a927A0b46c510e13746832CA8cf585e18f3"

# 任务书里的「声明值」（待证伪对象，来自推广文章）
CLAIMED_SHA = {
    1: "c22f8c74b0de", 2: "37eb121673db", 3: "356cdf33ca96",
    4: "83beeeb0b50e", 5: "83beeeb0b50e", 6: "c22f8c74b0de",
    7: "2e0a1609fb8f", 8: "9cad2f38db0e", 9: "5f62ced6d705",
    10: "5f62ced6d705", 11: "a5dd3b52cb1b", 12: "c2be8eefec19",
    13: "dccbd34519c3",
}
CLAIMED_MINTED = 40548
CLAIMED_CAP = 1_000_000

TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
ZERO_TOPIC = "0x" + "0" * 64

# ----------------------------------------------------------------------
# 纯 Python Keccak-256（审计独立性：不用项目代码）
# ----------------------------------------------------------------------
_MASK = (1 << 64) - 1
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
       0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
       0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
       0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
       0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
       0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _keccak_f(s):
    for rc in _RC:
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(s[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y] & _MASK)
                                               & b[(x + 2) % 5 + 5 * y])
        s[0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)
    s = [0] * 25
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            s[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(s)
    out = b""
    while len(out) < 32:
        for i in range(rate // 8):
            out += s[i].to_bytes(8, "little")
        if len(out) < 32:
            _keccak_f(s)
    return out[:32]


def selftest_keccak():
    assert keccak256(b"").hex() == ("c5d2460186f7233c927e7db2dcc703c0e500b6"
                                    "53ca82273b7bfad8045d85a470"), "空串向量失败"
    assert keccak256(b"abc").hex() == ("4e03657aea45a94fc7d47ba826c8d667c0d1e6e"
                                       "33a64a036ec44f58fa12d6c45"), "abc 向量失败"
    assert keccak256(b"name()")[:4].hex() == "06fdde03", "selector 自检失败"
    print("  [自检] 纯 Python Keccak-256：空串 / abc / name() selector 三个标准向量全对")


# ----------------------------------------------------------------------
# RPC
# ----------------------------------------------------------------------
def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "method": method, "params": params}).encode()
    last = None
    for attempt in range(2):  # 403 限流时退避重试一轮
        for url in RPCS:
            try:
                req = urllib.request.Request(url, data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    out = json.loads(r.read())
                if "error" in out:
                    code = out["error"].get("code") if isinstance(out["error"], dict) else None
                    if code == 3:  # 真正的执行 revert，所有节点结果一致，直接抛
                        raise RuntimeError("CALL_REVERT:" + str(out["error"])[:200])
                    # 限流/范围限制等节点级错误：换下一个节点
                    raise RuntimeError("NODE_ERR:" + str(out["error"])[:150])
                return out["result"]
            except Exception as e:  # noqa: BLE001
                last = e
                if str(e).startswith("CALL_REVERT"):
                    raise
        time.sleep(2)
    raise RuntimeError(f"RPC 传输失败: {last}")


def get_logs_range(address, topics, from_blk, to_blk, step=5000):
    """经 48.club 分段拉取历史日志（该节点支持 5000 块范围）"""
    out = []
    b = from_blk
    while b <= to_blk:
        e = min(b + step - 1, to_blk)
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                              "params": [{"address": address, "fromBlock": hex(b),
                                          "toBlock": hex(e), "topics": topics}]}).encode()
        req = urllib.request.Request(LOGS_RPC, data=payload, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                res = json.loads(r.read())
            if "error" in res:
                print(f"  ⚠️ 日志段 {b}-{e}: {str(res['error'])[:60]}")
            else:
                out.extend(res["result"])
        except Exception as ex:  # noqa: BLE001
            print(f"  ⚠️ 日志段 {b}-{e} 传输失败: {str(ex)[:50]}")
        b = e + 1
        time.sleep(0.12)
    return out


def eth_call(to, data, value=None, from_=None):
    tx = {"to": to, "data": data}
    if value is not None:
        tx["value"] = hex(value)
    if from_:
        tx["from"] = from_
    return rpc("eth_call", [tx, "latest"])


def abi_bytes(hexstr):
    raw = bytes.fromhex(hexstr[2:])
    if len(raw) < 64:
        return b""
    off = int.from_bytes(raw[:32], "big")
    ln = int.from_bytes(raw[off:off + 32], "big")
    return raw[off + 32: off + 32 + ln]


def sel(sig):
    return keccak256(sig.encode())[:4].hex()


EVID = {}
def rec(key, value):
    EVID[key] = value
    return value


def main():
    print("=" * 66)
    print("独立链上审计 —— ECREC / secp256k1 Auth Core（只用链上数据）")
    print("=" * 66)
    selftest_keccak()

    # --- 0. 网络 ---
    chain_id = int(rpc("eth_chainId", []), 16)
    print(f"\n[0] ChainID = {chain_id}  {'✓ BSC 主网' if chain_id == 56 else '✗ 不是 56!'}")
    rec("chainId", chain_id)

    # --- 1. 存在性 ---
    print("\n[1] 合约存在性")
    for label, addr in [("Circuits", CIRCUITS), ("Transistors", TRANSISTORS),
                        ("Witness", WITNESS), ("Factory", FACTORY)]:
        code = rpc("eth_getCode", [addr, "latest"])
        size = (len(code) - 2) // 2
        rec(f"code_{label}", {"addr": addr, "bytes": size})
        print(f"  {label:<12} {addr}  字节码 {size:,} B  "
              f"{'✓ 合约' if size > 0 else '✗ EOA/不存在'}")

    # name()/symbol()
    for fn, sig in [("name", "name()"), ("symbol", "symbol()")]:
        raw = eth_call(CIRCUITS, "0x" + sel(sig))
        val = abi_bytes(raw).decode()
        rec(fn, val)
        print(f"  {fn}() = {val!r}")

    # 创建交易
    print("\n[1b] 创建交易")
    tx = rpc("eth_getTransactionByHash", [CREATION_TX])
    receipt = rpc("eth_getTransactionReceipt", [CREATION_TX])
    blk = rpc("eth_getBlockByNumber", [tx["blockNumber"], False])
    ts = int(blk["timestamp"], 16)
    tstr = datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    rec("creation_tx", {"hash": CREATION_TX, "to": tx["to"], "from": tx["from"],
                        "block": int(tx["blockNumber"], 16), "time_utc8": tstr,
                        "status": receipt["status"], "gasUsed": int(receipt["gasUsed"], 16),
                        "n_logs": len(receipt["logs"])})
    print(f"  to={tx['to']}  {'✓ 是工厂' if tx['to'].lower() == FACTORY.lower() else '✗'}"
          f"  from={tx['from'][:10]}…")
    print(f"  区块 {int(tx['blockNumber'], 16)}  时间 {tstr} (UTC+8)  status={receipt['status']}")
    # 工厂索引：在回执日志里找 534
    idx_found = None
    for lg in receipt["logs"]:
        for t in lg["topics"]:
            if int(t, 16) == 534:
                idx_found = {"address": lg["address"], "topic": t}
        if int(lg["data"], 16) == 534 if lg["data"] != "0x" else False:
            idx_found = {"address": lg["address"], "data": lg["data"]}
    rec("factory_index_534", idx_found)
    print(f"  工厂索引 #534: {'✓ 回执日志中找到 ' + str(idx_found['address'][:10]) + '…' if idx_found else '✗ 未在回执日志中直接出现'}")

    # --- 2. 13 颗电路 sha256 ---
    print("\n[2] 电路注册 netlist(cid) 回读 + SHA-256 比对")
    netlist_sel = "3fc4be56"
    sha_results = {}
    for cid in range(1, 14):
        blob = abi_bytes(eth_call(CIRCUITS, "0x" + netlist_sel + f"{cid:064x}"))
        sha = hashlib.sha256(blob).hexdigest()
        ok = sha.startswith(CLAIMED_SHA[cid])
        sha_results[cid] = {"bytes": len(blob), "sha12": sha[:12],
                            "claimed": CLAIMED_SHA[cid], "match": ok}
        print(f"  cid{cid:<2} {len(blob):>6,} B  sha {sha[:12]}  "
              f"vs 声明 {CLAIMED_SHA[cid]}  {'✓' if ok else '✗ 不符'}")
    rec("netlists", sha_results)
    # cid0 / cid14 应不存在
    for cid in (0, 14):
        try:
            eth_call(CIRCUITS, "0x" + netlist_sel + f"{cid:064x}")
            print(f"  cid{cid}: ⚠️ 居然存在?")
            rec(f"cid{cid}_exists", True)
        except Exception as e:
            if str(e).startswith("CALL_REVERT"):
                print(f"  cid{cid}: 不存在（revert）✓")
                rec(f"cid{cid}_exists", False)
            else:
                print(f"  cid{cid}: 传输错误，无法判定 [{str(e)[:50]}]")
                rec(f"cid{cid}_exists", "unknown")

    # 创建回执日志原文（供工厂索引人工核查）
    rec("creation_receipt_logs", [
        {"address": lg["address"], "topics": lg["topics"], "data": lg["data"]}
        for lg in receipt["logs"]])
    print(f"  （创建回执含 {len(receipt['logs'])} 条日志，已存证据文件供核查工厂索引）")

    # --- 3. ERC-1155 ---
    print("\n[3] Transistors 代币合约")
    erc1155_ok = int(eth_call(TRANSISTORS, "0x01ffc9a7" + "d9b67a26" + "0" * 56), 16)
    rec("erc1155", bool(erc1155_ok))
    print(f"  supportsInterface(0xd9b67a26) = {bool(erc1155_ok)}  {'✓ ERC-1155' if erc1155_ok else '✗'}")
    for label, w in [("OKX主钱包", OKX_WALLET), ("MetaMask", MM_WALLET)]:
        for tid, tname in [(0, "NAND"), (1, "LATCH")]:
            data = "0x00fdd58e" + "0" * 24 + w[2:].lower() + f"{tid:064x}"
            bal = int(eth_call(TRANSISTORS, data), 16)
            print(f"  {label} {tname}(id{tid}) 余额 = {bal}")

    # 铸造事件统计（经 48.club 分段拉取历史日志）
    creation_blk = int(tx["blockNumber"], 16)
    latest = int(rpc("eth_blockNumber", []), 16)
    logs = get_logs_range(TRANSISTORS, [TRANSFER_SINGLE, None, ZERO_TOPIC],
                          creation_blk, latest)
    total = {0: 0, 1: 0}
    minters = {}
    latest_blk = 0
    for lg in logs:
        tid = int(lg["data"][:66], 16)
        val = int("0x" + lg["data"][66:], 16)
        total[tid] = total.get(tid, 0) + val
        to = "0x" + lg["topics"][3][-40:]
        minters[to] = minters.get(to, 0) + val
        latest_blk = max(latest_blk, int(lg["blockNumber"], 16))
    minted_sum = sum(total.values())
    rec("mints", {"by_id": total, "total": minted_sum, "events": len(logs),
                  "minters": minters, "latest_block": latest_blk})
    print(f"  TransferSingle 铸造事件 {len(logs)} 笔，合计 mint = {minted_sum:,}  "
          f"(NAND {total.get(0, 0):,} + LATCH {total.get(1, 0):,})  "
          f"{'✓ =40,548' if minted_sum == CLAIMED_MINTED else '✗ ≠40,548'}")
    for m, v in minters.items():
        print(f"    铸造者 {m}: {v:,}")

    # 单价模拟
    print("\n[3b] 单价/封顶模拟（eth_call mint，不下链）")
    mint_sel = "1b2ef1ca"
    RICH = "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"  # 币安热钱包，仅借余额模拟
    for label, tid, amt, value, frm in [
        ("正确价 mint(0,1) 付 2×1e14 wei", 0, 1, 2 * 10**14, OKX_WALLET),
        ("少付 mint(0,1) 付 1×1e14 wei", 0, 1, 1 * 10**14, OKX_WALLET),
        ("封顶内 mint(0, 959452)", 0, 959452, 959453 * 10**14, RICH),
        ("超封顶 mint(0, 959453)", 0, 959453, 959454 * 10**14, RICH),
    ]:
        data = "0x" + mint_sel + f"{tid:064x}{amt:064x}"
        try:
            eth_call(TRANSISTORS, data, value=value, from_=frm)
            print(f"  {label}: 不 revert")
            rec(f"mint_sim_{label}", "success")
        except Exception as e:
            kind = "revert" if str(e).startswith("CALL_REVERT") else "传输错误(无法判定)"
            print(f"  {label}: {kind}  [{str(e)[:70]}]")
            rec(f"mint_sim_{label}", f"{kind}: {str(e)[:120]}")
        time.sleep(0.5)

    # --- 4. cid7 Keccak 功能（组合电路，204 引脚 = 3×64 + 5 lane + 5 round + 2 mode）---
    print("\n[4] cid7 keccak_comp 链上功能复现（witness evaluate，LSB-first 打包）")
    ev_sel = sel("evaluate(address,uint256,bytes)")
    rec("evaluate_selector", ev_sel)
    print(f"  计算得 evaluate selector = 0x{ev_sel}")
    import random
    random.seed(20260819)

    def chain_eval(inputs204):
        assert len(inputs204) == 204
        buf = bytearray(26)
        for i, bit in enumerate(inputs204):
            if bit:
                buf[i // 8] |= 1 << (i % 8)
        enc = ("0x" + ev_sel + "0" * 24 + CIRCUITS[2:] + f"{7:064x}" + f"{96:064x}"
               + f"{len(buf):064x}" + buf.hex() + "0" * ((32 - len(buf) % 32) % 32 * 2))
        out = abi_bytes(eth_call(WITNESS, enc))
        return [(out[i // 8] >> (i % 8)) & 1 for i in range(64)]

    eval_ok = 0
    eval_total = 0
    # 3 组 XOR3 模式（mode=01）+ 1 组 χ 模式（mode=00）
    for trial in range(3):
        a = [random.choice([0, 1]) for _ in range(64)]
        b = [random.choice([0, 1]) for _ in range(64)]
        c = [random.choice([0, 1]) for _ in range(64)]
        lane = [random.choice([0, 1]) for _ in range(5)]
        rnd = [random.choice([0, 1]) for _ in range(5)]
        got = chain_eval(a + b + c + lane + rnd + [1, 0])
        expect = [x ^ y ^ z for x, y, z in zip(a, b, c)]
        eval_total += 1
        eval_ok += got == expect
        print(f"  XOR3 向量{trial + 1}: out==a^b^c ? {got == expect}")
        time.sleep(0.3)
    a = [random.choice([0, 1]) for _ in range(64)]
    b = [random.choice([0, 1]) for _ in range(64)]
    c = [random.choice([0, 1]) for _ in range(64)]
    got = chain_eval(a + b + c + [0] * 10 + [0, 0])
    expect = [x ^ ((1 - y) & z) for x, y, z in zip(a, b, c)]
    eval_total += 1
    eval_ok += got == expect
    print(f"  χ 向量: out==a^(~b&c) ? {got == expect}")
    rec("cid7_eval", {"passed": eval_ok, "total": eval_total})
    print(f"  结果: {eval_ok}/{eval_total} 组随机向量链上复现")

    # --- 5. Circuits 合约交互记录（事件，分段拉取）---
    print("\n[5] Circuits 合约事件日志（全部）")
    clogs = get_logs_range(CIRCUITS, [], creation_blk, latest)
    senders = {}
    for lg in clogs:
        txh = lg["transactionHash"]
        if txh not in senders:
            t = rpc("eth_getTransactionByHash", [txh])
            senders[txh] = t["from"] if t else "?"
    froms = {}
    for txh, f in senders.items():
        froms[f.lower()] = froms.get(f.lower(), 0) + 1
    rec("circuits_logs", {"n_logs": len(clogs), "tx_senders": froms})
    print(f"  事件 {len(clogs)} 条，交易发送者分布: {froms}")

    # --- 5b. 合约余额侧证 ---
    print("\n[5b] 合约余额")
    for label, addr in [("本处理器 Circuits", CIRCUITS),
                        ("老橘子 KECCAK 库", "0xec9C5A1602E92Db89E3fb61652019fb3AC30d075")]:
        bal = int(rpc("eth_getBalance", [addr, "latest"]), 16)
        rec(f"balance_{label}", bal / 1e18)
        print(f"  {label}: {bal / 1e18:.4f} BNB")

    out = ROOT / "docs" / "audit_evidence.json"
    out.write_text(json.dumps(EVID, indent=2, ensure_ascii=False, default=str))
    print(f"\n证据已存 {out}")


if __name__ == "__main__":
    main()
