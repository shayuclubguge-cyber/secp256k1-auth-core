"""
Keccak Core 一键发射脚本 —— 创建处理器 → 验证（纯复用，无铸币/流片）

与 ModMath 的关键区别：Keccak Core 的 6 颗零件（ECREC v2 cid 7/9/10/11/12/13）
已在链上验证，全部通过 REF 复用，本处理器无需铸造晶体管、无需流片新电路。
因此只有 TX1 一笔交易（create，0.001 BNB deployFee + gas）。

用法（按序执行，create 会弹钱包，等用户点确认）：
  python scripts/keccak_core_launch.py precheck   # eth_call 预检（不花钱）
  python scripts/keccak_core_launch.py create     # TX1 创建处理器（0.001 BNB）
  python scripts/keccak_core_launch.py verify     # 链上回读 name/symbol（只读）

状态文件：vectors/keccak_core_launch_state.json（记录新合约地址与 tx 哈希）
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "vectors" / "keccak_core_launch_state.json"

FROM = "0x6EB36a927A0b46c510e13746832CA8cf585e18f3"   # MetaMask（与 ModMath 同一操作钱包）
FACTORY = "0x68224f668083c29e9800be2a646d42d18cedf7e2"
CREATE_SELECTOR = "47f9b5fd"      # create(string,string,string,uint256,uint256)
NAME_SELECTOR = "06fdde03"        # name()
SYMBOL_SELECTOR = "95d89b41"      # symbol()
CPU_EVENT_TOPIC0 = "2e8868f18a1eaf0222b5b09484fdf12163e9741393f8457cd79d2ae42b2d2290"

NAME = "Keccak Core"              # 严格大小写 + 空格分隔（任务硬性要求）
SYMBOL = "KCCORE"                 # 用户 2026-08-19 确认（避开老橘子 KECCAK 库）
STORY = ("Keccak-256 deconstructed — five primitive parts + stitch "
         "controller, REF-stitched raw NAND for custom hash variants")  # 候选 A，115 字符
CAP = 1_000_000
PRICE = 10**14                    # 0.0001 BNB（与 ModMath/ECREC 同参数）

RPCS = ["https://bsc-dataseed.binance.org", "https://bsc.publicnode.com"]
BRIDGE = "http://127.0.0.1:10086/command"
SESSION = "keccak-core-launch"


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
    head = ("a0".rjust(64, "0") + "e0".rjust(64, "0") + "120".rjust(64, "0")
            + CAP.to_bytes(32, "big").hex() + PRICE.to_bytes(32, "big").hex())
    return "0x" + CREATE_SELECTOR + head + \
        encode_string(NAME) + encode_string(SYMBOL) + encode_string(STORY)


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


def decode_abi_string(hexresult):
    raw = bytes.fromhex(hexresult[2:])
    offset = int.from_bytes(raw[:32], "big")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    return raw[offset + 32: offset + 32 + length].decode()


# ---------------------------------------------------------------------------
def step_precheck():
    print("== 预检（eth_call，不花钱）==")
    print(f"  名称: 「{NAME}」  symbol: {SYMBOL}")
    print(f"  简介（{len(STORY)} 字符）: {STORY}")
    assert NAME == "Keccak Core", "名称必须严格为 Keccak Core"
    assert len(STORY) <= 120, "简介超过 120 字符限制"
    ok1, r1 = eth_call_check(FACTORY, create_data(), 10**15)
    print(f"  {'✓' if ok1 else '✗'} 创建处理器 create(..., cap={CAP:,}, "
          f"price=0.0001): {r1 if not ok1 else '通过（0.001 BNB deployFee 可执行）'}")
    print("  零件复用: ECREC v2 cid 7/9/10/11/12/13（已验证，见 "
          "docs/KECCAK_CORE_VERIFY_REPORT.md），本处理器 0 铸币 0 流片")


def step_create():
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
              reuse_cids=[7, 9, 10, 11, 12, 13],
              reuse_from="0xa3b6d9121146c29fb236001b93a45cb7a78a2247")
    save_state(st)
    print(f"   Circuits:   {circuits}")
    print(f"   Transistors: {transistors}")
    print(f"   官网: https://tapeout.net/#p/{circuits}")
    print("   （无需 mint/tapeout/withdraw —— 6 颗零件全部 REF 复用 ECREC v2）")


def step_verify():
    st = load_state()
    assert st.get("circuits"), "先跑 create"
    print(f"== 链上回读 ==  {st['circuits']}")
    name_r = rpc("eth_call", [{"to": st["circuits"],
                               "data": "0x" + NAME_SELECTOR}, "latest"])
    sym_r = rpc("eth_call", [{"to": st["circuits"],
                              "data": "0x" + SYMBOL_SELECTOR}, "latest"])
    if name_r:
        name = decode_abi_string(name_r)
        ok = name == NAME
        print(f"  {'✓' if ok else '✗'} name()   = 「{name}」"
              f"{'' if ok else f'（期望「{NAME}」）'}")
        assert ok, "name 不一致！"
    else:
        print("  … name() 读取失败（合约可能未实现 ERC20 metadata，"
              "以官网页面显示为准）")
    if sym_r:
        sym = decode_abi_string(sym_r)
        ok = sym == SYMBOL
        print(f"  {'✓' if ok else '✗'} symbol() = 「{sym}」"
              f"{'' if ok else f'（期望「{SYMBOL}」）'}")
        assert ok, "symbol 不一致！"
    print(f"  官网: https://tapeout.net/#p/{st['circuits']}")
    print("  请人工核对：简介显示完整（无截断/乱码），名称大小写为 Keccak Core")


if __name__ == "__main__":
    steps = {"precheck": step_precheck, "create": step_create,
             "verify": step_verify}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        print(__doc__)
        sys.exit(1)
    steps[sys.argv[1]]()
