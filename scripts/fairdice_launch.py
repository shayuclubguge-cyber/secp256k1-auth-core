"""
FairDice Core 一键发射脚本 —— 创建处理器 → 铸币 → 流片 → 回款 → 验证

与 ModMath 的关键区别：主控壳含 3 个 LATCH（mod-6 累加器），
铸币需 NAND + LATCH 两笔。

⚠️ 简介（story）未定稿：创建前必须由用户在候选 A/B/C 中拍板，
   填入下方 STORY（创建后不可改——ModMath v1 曾因文案不佳废弃重建）。
   STORY 为 None 时 create/precheck 会直接拒绝执行。

用法（按序执行，每步都会弹钱包，等用户点确认）：
  python scripts/fairdice_launch.py precheck   # 全部 eth_call 预检（不花钱）
  python scripts/fairdice_launch.py create     # TX1 创建处理器（0.001 BNB）
  python scripts/fairdice_launch.py mint       # TX2 铸 842 NAND + TX3 铸 3 LATCH（可回收）
  python scripts/fairdice_launch.py tapeout    # TX4 流片主控壳（仅 gas）
  python scripts/fairdice_launch.py withdraw   # TX5 回款闭环
  python scripts/fairdice_launch.py verify     # 链上回读比对（只读）

状态文件：vectors/fairdice_launch_state.json（记录新合约地址与 tx 哈希）
"""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "vectors" / "fairdice_launch_state.json"
NET_PATH = ROOT / "vectors" / "fairdice_ctrl.net"

FROM = "0x6EB36a927A0b46c510e13746832CA8cf585e18f3"   # MetaMask（与 ECREC/ModMath/Keccak 同钱包）
FACTORY = "0x68224f668083c29e9800be2a646d42d18cedf7e2"
CREATE_SELECTOR = "47f9b5fd"      # create(string,string,string,uint256,uint256)
TAPEOUT_SELECTOR = "7bd3ac1d"     # 流片
MINT_SELECTOR = "1b2ef1ca"        # mint(uint256 id, uint256 amount)
WITHDRAW_SELECTOR = "3ccfd60b"    # withdraw()
NETLIST_SELECTOR = "3fc4be56"     # netlist(uint256)
CPU_EVENT_TOPIC0 = "2e8868f18a1eaf0222b5b09484fdf12163e9741393f8457cd79d2ae42b2d2290"

NAME = "FairDice Core"            # 严格大小写 + 空格分隔（任务硬性要求）
SYMBOL = "DICE"                   # 候选 "DICE"（备选 "FAIR"；链上无 symbol 冲突查询接口，
                                  #  与老橘子 KECCAK/SHA256 库及自有 ECREC/MMAT/KCCORE 均不冲突）

# 简介定稿（用户 2026-08-20 确认 V1，120 字符顶格，创建后不可改）：
STORY = ("Sign → Hash → Roll 1-6. Your wallet signature is the random seed. "
         "Real circuits do the math. No server, no manipulation.")

CAP = 1_000_000
PRICE = 10**14                    # 0.0001 BNB（与 ECREC/ModMath/Keccak 同参数）
NAND_NEED = 842                   # 主控壳精确 NAND 数（见 make_fairdice_netlist.py）
LATCH_NEED = 3                    # 主控壳 LATCH 数（mod-6 累加器）
N_IN, N_OUT = 74, 83              # 主控壳引脚

RPCS = ["https://bsc-dataseed.binance.org", "https://bsc.publicnode.com"]
BRIDGE = "http://127.0.0.1:10086/command"
SESSION = "fairdice-launch"


# ---------------------------------------------------------------------------
def rpc(method, params):
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method,
                 "params": params}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            if "error" not in out:
                return out.get("result")
        except Exception:
            time.sleep(1)
    return None


def bridge_eval(code, timeout=15):
    req = urllib.request.Request(
        BRIDGE, data=json.dumps({"action": "evaluate", "args": {"code": code},
                                 "session": SESSION}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    assert resp.get("ok"), resp
    return resp["data"]


def send_tx(to, data, value_wei=0, gas=600_000):
    """通过 WebBridge 发交易（钱包弹窗由用户确认），返回 tx hash。
    双插件共存时用 EIP-6963 精确选 MetaMask（rdns=io.metamask），
    gasPrice 显式 0.15 gwei（HANDOFF 规则 3）"""
    js = f"""
window.__txV = {{status:'pending'}};
(async () => {{
  const w = await new Promise((resolve) => {{
    const onAnn = (e) => {{
      if (e.detail && e.detail.info && e.detail.info.rdns === 'io.metamask')
        resolve(e.detail.provider);
    }};
    window.addEventListener('eip6963:announceProvider', onAnn);
    window.dispatchEvent(new Event('eip6963:requestProvider'));
    setTimeout(() => resolve(window.ethereum), 800);
  }});
  try {{
    const hash = await w.request({{method:'eth_sendTransaction', params:[{{
      from:'{FROM}', to:'{to}',
      data:'{data}', value:'{hex(value_wei)}', gas:'{hex(gas)}',
      gasPrice:'0x08f0d180'}}]}});
    window.__txV = {{status:'sent', hash}};
  }} catch(e) {{ window.__txV = {{status:'error', msg:String(e&&(e.message||e))}}; }}
}})();
'started'
"""
    assert bridge_eval(js)["value"] == "started"
    for _ in range(120):               # 最多等 6 分钟用户确认
        time.sleep(3)
        v = bridge_eval("window.__txV")["value"]
        if v["status"] == "sent":
            return v["hash"]
        if v["status"] == "error":
            raise RuntimeError("钱包拒绝/失败: " + v["msg"])
    raise TimeoutError("等钱包确认超时（6 分钟）")


def wait_receipt(tx):
    for _ in range(60):
        rc = rpc("eth_getTransactionReceipt", [tx])
        if rc:
            return rc
        time.sleep(2)
    raise TimeoutError("等回执超时")


# ---------------------------------------------------------------------------
def encode_string(s):
    b = s.encode()
    pad = (32 - len(b) % 32) % 32
    return len(b).to_bytes(32, "big").hex() + b.hex() + "00" * pad


def create_data():
    assert STORY is not None, \
        "简介未定稿！请在 scripts/fairdice_launch.py 中填入 STORY（候选 A/B/C 见 STORY_CANDIDATES）"
    head = ("a0".rjust(64, "0") + "e0".rjust(64, "0") + "120".rjust(64, "0")
            + CAP.to_bytes(32, "big").hex() + PRICE.to_bytes(32, "big").hex())
    return "0x" + CREATE_SELECTOR + head + \
        encode_string(NAME) + encode_string(SYMBOL) + encode_string(STORY)


def mint_data(tid, amount):
    return "0x" + MINT_SELECTOR + tid.to_bytes(32, "big").hex() \
        + amount.to_bytes(32, "big").hex()


def tapeout_data(net, n_in, n_out):
    pad = (32 - len(net) % 32) % 32
    return ("0x" + TAPEOUT_SELECTOR + (96).to_bytes(32, "big").hex()
            + n_in.to_bytes(32, "big").hex() + n_out.to_bytes(32, "big").hex()
            + len(net).to_bytes(32, "big").hex() + net.hex() + "00" * pad)


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(st):
    STATE_PATH.write_text(json.dumps(st, indent=2))


def eth_call_check(to, data, value_wei=0):
    """eth_call 预检：返回 (ok, result_or_error)"""
    tx = {"from": FROM, "to": to, "data": data}
    if value_wei:
        tx["value"] = hex(value_wei)
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                 "params": [tx, "latest"]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            if "error" in out:
                return False, out["error"]
            return True, out.get("result")
        except Exception as e:
            last = str(e)
    return False, last


# ---------------------------------------------------------------------------
def step_precheck():
    print("== 预检（eth_call，不花钱）==")
    print(f"  名称: 「{NAME}」  symbol: {SYMBOL}")
    print(f"  简介（{len(STORY)} 字符）: {STORY}")
    assert len(STORY) <= 120, "简介超过 120 字符限制"
    ok1, r1 = eth_call_check(FACTORY, create_data(), 10**15)
    print(f"  {'✓' if ok1 else '✗'} 创建处理器 create(..., cap={CAP:,}, "
          f"price=0.0001): {r1 if not ok1 else '通过（0.001 BNB deployFee 可执行）'}")
    net = NET_PATH.read_bytes()
    sha = hashlib.sha256(net).hexdigest()
    print(f"  主控壳网表: {len(net):,}B  sha256={sha[:16]}…  "
          f"预估流片 gas {421 * len(net):,}")
    st = load_state()
    if st.get("transistors"):
        ok2, r2 = eth_call_check(st["transistors"], mint_data(0, NAND_NEED),
                                 (NAND_NEED + 1) * 10**14)
        print(f"  {'✓' if ok2 else '✗'} mint(0, {NAND_NEED} NAND): "
              f"{r2 if not ok2 else '通过'}")
        ok3, r3 = eth_call_check(st["transistors"], mint_data(1, LATCH_NEED),
                                 (LATCH_NEED + 1) * 10**14)
        print(f"  {'✓' if ok3 else '✗'} mint(1, {LATCH_NEED} LATCH): "
              f"{r3 if not ok3 else '通过'}")
    if st.get("circuits"):
        ok4, r4 = eth_call_check(st["circuits"], tapeout_data(net, N_IN, N_OUT))
        print(f"  {'✓' if ok4 else '✗'} 流片主控壳（{len(net)}B）: "
              f"{r4 if not ok4 else '通过'}")
    if not st:
        print("  （尚未创建处理器，mint/流片预检将在 create 后可用）")


def step_create():
    assert STORY is not None, "简介未定稿！先填 STORY（候选见 precheck 输出）"
    print(f"== TX1 创建处理器 ==  名称「{NAME}」 symbol {SYMBOL}  "
          f"cap {CAP:,}  单价 0.0001 BNB")
    print(f"   简介: {STORY}")
    print("   钱包弹窗金额: 0.001 BNB（deployFee）+ gas —— 请点确认")
    h = send_tx(FACTORY, create_data(), 10**15, 1_200_000)
    print("   tx:", h)
    rc = wait_receipt(h)
    assert rc["status"] == "0x1", f"创建失败: {rc}"
    print(f"   ✓ 已确认（gas {int(rc['gasUsed'], 16):,}）")
    circuits = transistors = None
    for lg in rc["logs"]:
        if lg["address"].lower() == FACTORY.lower() and \
           lg["topics"][0][2:] == CPU_EVENT_TOPIC0:
            circuits = "0x" + lg["topics"][1][26:]
            transistors = "0x" + lg["topics"][2][26:]
    assert circuits and transistors, "未在事件中找到新合约地址"
    st = load_state()
    st.update(name=NAME, symbol=SYMBOL, story=STORY, circuits=circuits,
              transistors=transistors, create_tx=h,
              ref_cids=[3, 6, 8, 13],
              ref_from="0xa3b6d9121146c29fb236001b93a45cb7a78a2247")
    save_state(st)
    print(f"   Circuits:   {circuits}")
    print(f"   Transistors: {transistors}")
    print(f"   官网: https://tapeout.net/#p/{circuits}")


def step_mint():
    st = load_state()
    assert st.get("transistors"), "先跑 create"
    # TX2：NAND
    value = (NAND_NEED + 1) * 10**14
    print(f"== TX2 铸 NAND ==  mint(0, {NAND_NEED})  到 {st['transistors']}")
    print(f"   钱包弹窗金额: {value / 1e18} BNB（其中 {NAND_NEED * 1e14 / 1e18} "
          f"走 withdraw 可回收，净成本仅 0.0001 协议费）—— 请点确认")
    h = send_tx(st["transistors"], mint_data(0, NAND_NEED), value, 600_000)
    print("   tx:", h)
    rc = wait_receipt(h)
    assert rc["status"] == "0x1", f"mint NAND 失败: {rc}"
    print(f"   ✓ NAND 已铸（gas {int(rc['gasUsed'], 16):,}）")
    st["mint_nand_tx"] = h
    save_state(st)
    # TX3：LATCH
    value = (LATCH_NEED + 1) * 10**14
    print(f"== TX3 铸 LATCH ==  mint(1, {LATCH_NEED})")
    print(f"   钱包弹窗金额: {value / 1e18} BNB（可回收）—— 请点确认")
    h = send_tx(st["transistors"], mint_data(1, LATCH_NEED), value, 600_000)
    print("   tx:", h)
    rc = wait_receipt(h)
    assert rc["status"] == "0x1", f"mint LATCH 失败: {rc}"
    print(f"   ✓ LATCH 已铸（gas {int(rc['gasUsed'], 16):,}）")
    st["mint_latch_tx"] = h
    save_state(st)


def step_tapeout():
    st = load_state()
    assert st.get("circuits"), "先跑 create"
    net = NET_PATH.read_bytes()
    sha = hashlib.sha256(net).hexdigest()
    print(f"== TX4 流片 ==  fairdice_ctrl 主控壳 {len(net):,}B  "
          f"{N_IN}→{N_OUT}  sha256={sha[:16]}…")
    print(f"   到 {st['circuits']}，烧 {NAND_NEED} NAND + {LATCH_NEED} LATCH")
    print(f"   钱包弹窗金额: 0 BNB（仅 gas，预估 ~{421 * len(net) / 1e6:.1f}M）"
          "—— 请点确认")
    h = send_tx(st["circuits"], tapeout_data(net, N_IN, N_OUT), 0, 5_000_000)
    print("   tx:", h)
    rc = wait_receipt(h)
    assert rc["status"] == "0x1", f"流片失败: {rc}"
    print(f"   ✓ 已确认（gas {int(rc['gasUsed'], 16):,}）")
    st["tapeout_tx"] = h
    st["cid"] = 1                     # 新处理器第一颗电路
    save_state(st)
    print("   主控壳 cid = 1（新处理器首颗电路）")


def step_withdraw():
    st = load_state()
    assert st.get("transistors"), "先跑 create"
    print("== TX5 回款 ==  withdraw() 收回 mint 款")
    print("   钱包弹窗金额: 0 BNB（仅 gas）—— 请点确认")
    h = send_tx(st["transistors"], "0x" + WITHDRAW_SELECTOR, 0, 100_000)
    print("   tx:", h)
    rc = wait_receipt(h)
    assert rc["status"] == "0x1", f"withdraw 失败: {rc}"
    print(f"   ✓ 已确认（gas {int(rc['gasUsed'], 16):,}）")
    st["withdraw_tx"] = h
    save_state(st)


def step_verify():
    st = load_state()
    assert st.get("circuits") and st.get("cid"), "先跑 tapeout"
    data = "0x" + NETLIST_SELECTOR + f"{st['cid']:064x}"
    result = rpc("eth_call", [{"to": st["circuits"], "data": data}, "latest"])
    assert result, "链上读取失败"
    raw = bytes.fromhex(result[2:])
    offset = int.from_bytes(raw[:32], "big")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    blob = raw[offset + 32: offset + 32 + length]
    local = NET_PATH.read_bytes()
    chain_sha = hashlib.sha256(blob).hexdigest()
    local_sha = hashlib.sha256(local).hexdigest()
    print(f"== 链上回读 ==  cid{st['cid']} @ {st['circuits']}")
    print(f"   链上 {len(blob):,}B  sha256={chain_sha}")
    print(f"   本地 {len(local):,}B  sha256={local_sha}")
    assert blob == local, "字节不一致！"
    print("   ✓ 链上 = 本地（逐字节一致）")
    print(f"   官网: https://tapeout.net/#p/{st['circuits']}")
    print("   请人工核对：简介显示完整（无截断/乱码），名称大小写为 FairDice Core")


if __name__ == "__main__":
    steps = {"precheck": step_precheck, "create": step_create,
             "mint": step_mint, "tapeout": step_tapeout,
             "withdraw": step_withdraw, "verify": step_verify}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        print(__doc__)
        sys.exit(1)
    steps[sys.argv[1]]()
