"""
keccak_link 拍级验证：64 字节 → Keccak-256 摘要（流式单电路）

结构：
  ABSORB(25) → 24×[P1 θ累加(25) → P2 θ施加+ρ(25) → P3 π(50) → P4 χ+ι(27)]
  → SQUEEZE(4) → DONE

全局拍号（v3 双环）：
  beat 0        : start=1（IDLE→ABS）
  beat 1..25    : ABSORB（atick = beat-1）
  轮 r          : base = 26 + 100r，rtick = beat-base（0..99）
                  P1 [0,25) P2 [25,50) P3 [50,75) P4 [75,100)
  SQUEEZE       : beat 2426..2429（stick 0..3，out_word = lane 0..3）
  DONE          : beat 2430 起 done=1

探针（拍末 latch_values）：
  ABSORB 后     : ringA 槽位 j == 填充后车道 j
  P1 后         : cd 槽位 x == C[x]
  P2 后         : ringA 槽位 t == rotl(A[t]^D[t%5], ρ[t])
  P3 后         : ringB 槽位 w == B[w]（π 后）
  P4 后(轮末)   : ringA 槽位 j == 黄金轮输出车道 j
"""

import sys
import time

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1/tests")

from tapeout.engine import keccak256 as engine_keccak256
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.keccak_link import (
    build_keccak_link, keccak256_golden, keccak_f_lanes, rotl64,
    KECCAK_RC, RHO, PI_INV,
)
from test_keccak_component import build_keccak_component

# v2 收费版 CPU；keccak 组件目标 cid7（本地验证用占位 id 即可，只要 resolver 一致）
V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
KC_CID = 7
MASK64 = (1 << 64) - 1

MSG = bytes(range(64))  # 64 字节测试输入（Qx‖Qy 形态）


def padded_lanes(data64: bytes):
    lanes = [0] * 25
    for i in range(8):
        lanes[i] = int.from_bytes(data64[8 * i:8 * i + 8], "little")
    lanes[8] ^= 0x01
    lanes[16] ^= 0x8000000000000000
    return lanes


def round_trace(lanes_in, r):
    """返回 (C, D, theta_rho, B_pi, lanes_out)"""
    C = [0] * 5
    for x in range(5):
        for y in range(5):
            C[x] ^= lanes_in[x + 5 * y]
    D = [C[(x - 1) % 5] ^ rotl64(C[(x + 1) % 5], 1) for x in range(5)]
    tr = [lanes_in[l] ^ D[l % 5] for l in range(25)]
    tr = [rotl64(tr[l], RHO[l]) for l in range(25)]
    B = [0] * 25
    for w in range(25):
        B[w] = tr[PI_INV[w]]
    out = keccak_f_lanes(list(lanes_in), r)
    return C, D, tr, B, out


def bus_val(lv, sigs):
    return sum(int(lv.get(s.id, False)) << k for k, s in enumerate(sigs))


def slots(lv, bus, n, width=64):
    return [bus_val(lv, bus[width * j:width * (j + 1)]) for j in range(n)]


def detect_offset(actual, expected):
    """actual[s] == expected[(s+k)%n] 的 k；无则 None"""
    n = len(expected)
    for k in range(n):
        if all(actual[s] == expected[(s + k) % n] for s in range(n)):
            return k
    return None


def report_phase(name, actual, expected):
    if actual == expected:
        print(f"    {name}: ✓")
        return True
    off = detect_offset(actual, expected)
    nm = sum(1 for a, e in zip(actual, expected) if a != e)
    print(f"    {name}: ✗ {nm}/{len(expected)} 槽不符"
          + (f"，但整体旋转偏移 k={off}" if off is not None else ""))
    for s in range(len(expected)):
        if actual[s] != expected[s]:
            print(f"      槽{s}: 实 {actual[s]:016x} 期 {expected[s]:016x}")
            if s >= 3:
                print("      ...")
                break
    return False


def run_one(sim, probes, msg, probe=False):
    """跑一条 64 字节消息，返回 (digest_bytes, 探针全过?)"""
    st_bus, stb_bus, cd_bus = probes["st"], probes["stb"], probes["cd"]
    lanes = padded_lanes(msg)
    gold = list(lanes)

    def tick(word=0, start=False):
        ins = [bool((word >> k) & 1) for k in range(64)] + [bool(start)]
        return sim.beat(ins)

    def absorb_word(t):
        if t < 8:
            return lanes[t]
        if t == 8:
            return 0x01
        if t == 16:
            return 0x8000000000000000
        return 0

    tick(start=True)                      # beat 0
    for t in range(25):                   # beat 1..25 ABSORB
        tick(absorb_word(t))

    ok = True
    if probe:
        ok &= report_phase("ABSORB→ringA",
                           slots(sim.latch_values, st_bus, 25), lanes)

    for r in range(24):
        C, D, tr, B, gold = round_trace(gold, r)
        for rt in range(100):
            tick(KECCAK_RC[r] if rt == 75 else 0)
            if probe and r == 0 and rt in (24, 49, 74, 99):
                lv = sim.latch_values
                if rt == 24:
                    ok &= report_phase("R0 P1→cd", slots(lv, cd_bus, 5), C)
                elif rt == 49:
                    ok &= report_phase("R0 P2→ringA",
                                       slots(lv, st_bus, 25), tr)
                elif rt == 74:
                    ok &= report_phase("R0 P3→ringB",
                                       slots(lv, stb_bus, 25), B)
                else:
                    ok &= report_phase("R0 P4→ringA",
                                       slots(lv, st_bus, 25), gold)
        if probe and r == 0 and not ok:
            print("  ✗ 第 0 轮已偏离，停止（后续轮次无意义）")
            return None, False

    digest = []
    for i in range(4):                    # SQUEEZE 4 拍
        out = tick()
        digest.append(sum(int(out[k]) << k for k in range(64)))
    out = tick()
    assert out[65], "done 未置位"
    return b"".join(w.to_bytes(8, "little") for w in digest), ok


def main():
    t0 = time.time()

    # ---------- 黄金模型自检 ----------
    for msg in (b"\x00" * 64, MSG):
        lanes = keccak256_golden(msg)
        dig = b"".join(lanes[i].to_bytes(8, "little") for i in range(4))
        assert dig == engine_keccak256(msg), "黄金模型自检失败"
    print("✓ 黄金模型 == engine.keccak256（2 组）")

    # ---------- 结构 ----------
    comp, _ = build_keccak_component()
    c, probes = build_keccak_link(V2_CPU, KC_CID)
    nb = len(c.to_bytes())
    print(f"✓ 电路: {len(c.instructions)} 指令 / {c.gate_count()} 门 / "
          f"{nb} 字节 / {c.n_in} 入 {c.n_out} 出")

    resolver = REFResolver()
    resolver.register(V2_CPU, KC_CID, comp)
    sim = SimWithREF(c, resolver)

    print("  消息1（带逐阶段探针）...")
    got, ok = run_one(sim, probes, MSG, probe=True)
    assert ok, "探针阶段未全过"
    want = engine_keccak256(MSG)
    print(f"  摘要: {got.hex()}")
    print(f"  期望: {want.hex()}")
    assert got == want, "摘要不符"

    # ---------- 更多向量（仅比对摘要） ----------
    vectors = [
        b"\x00" * 64,
        b"\xff" * 64,
        bytes(reversed(range(64))),
        engine_keccak256(b"hello tapeout") + engine_keccak256(b"tapeout")[:32],
    ]
    for i, msg in enumerate(vectors):
        sim.reset()
        got, _ = run_one(sim, probes, msg)
        want = engine_keccak256(msg)
        assert got == want, f"向量{i} 摘要不符: {got.hex()} != {want.hex()}"
        print(f"✓ 向量{i+2}: {got.hex()[:32]}...")

    print(f"✓ keccak_link 全绿（{time.time() - t0:.1f}s）")


if __name__ == "__main__":
    main()
