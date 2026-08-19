"""
kl_split 拍级验证：拆分版 keccak_link（5 颗 REF 零件）== 黄金模型

与 test_keccak_link.py 同一外部协议，仅两处驱动差异：
  RC 窗 rtick 75 → 76；SQUEEZE 4 → 5 拍（done 晚 1 拍）。

探针按「物理槽 = 逻辑车道 + 1」约定换算：
  ringA 探针点在 rt 50（P2 末写拍完）、rt 99（轮末）
  ringB 探针点在 rt 75（B[24] 落槽后，恰为恒等排列）
  CD     探针点在 rt 24（CD[s] = C[(s−1)%5]）
"""

import sys
import time

sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1")
sys.path.insert(0, "/Users/zhanghaocheng/Documents/kimi/workspace/tapeout_secp256k1/tests")

from tapeout.engine import keccak256 as engine_keccak256
from tapeout.simulator import REFResolver, SimWithREF
from tapeout.parts.keccak_link import (
    keccak256_golden, keccak_f_lanes, rotl64, KECCAK_RC, RHO, PI_INV,
)
from tapeout.parts.kl_split import (
    build_kl_ringa_lo, build_kl_ringa_hi, build_kl_rho,
    build_kl_ringb, build_kl_top,
)
from test_keccak_component import build_keccak_component

V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")
KC_CID = 7          # keccak 组件（v2 链上 cid7）
RA_LO_CID = 9       # kl_ringa_lo 链上 cid9
RA_HI_CID = 10      # kl_ringa_hi 链上 cid10
RHO_CID = 11        # kl_rho 链上 cid11
RB_CID = 12         # kl_ringb 链上 cid12

MSG = bytes(range(64))


def padded_lanes(data64: bytes):
    lanes = [0] * 25
    for i in range(8):
        lanes[i] = int.from_bytes(data64[8 * i:8 * i + 8], "little")
    lanes[8] ^= 0x01
    lanes[16] ^= 0x8000000000000000
    return lanes


def round_trace(lanes_in, r):
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


def phys_to_logical(phys):
    """物理槽 s 存逻辑车道 (s−1) → logical[ℓ] = phys[(ℓ+1)%25]"""
    n = len(phys)
    return [phys[(l + 1) % n] for l in range(n)]


def report_phase(name, actual, expected):
    if actual == expected:
        print(f"    {name}: ✓")
        return True
    nm = sum(1 for a, e in zip(actual, expected) if a != e)
    print(f"    {name}: ✗ {nm}/{len(expected)} 槽不符")
    for s in range(len(expected)):
        if actual[s] != expected[s]:
            print(f"      槽{s}: 实 {actual[s]:016x} 期 {expected[s]:016x}")
            if s >= 3:
                print("      ...")
                break
    return False


def run_one(sim, probes, msg, probe=False):
    st_lo, st_hi = probes["st_lo"], probes["st_hi"]
    stb_bus, cd_bus = probes["stb"], probes["cd"]

    def subs():
        return (sim._sub_sims[(V2_CPU, RA_LO_CID)],
                sim._sub_sims[(V2_CPU, RA_HI_CID)],
                sim._sub_sims[(V2_CPU, RB_CID)])

    def ringa_phys():
        """ringA 25 物理槽 × 64 位 = lo 位片 | hi 位片 << 32"""
        sub_lo, sub_hi, _ = subs()
        lo = slots(sub_lo.latch_values, st_lo, 25, width=32)
        hi = slots(sub_hi.latch_values, st_hi, 25, width=32)
        return [lo[s] | (hi[s] << 32) for s in range(25)]
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
        ok &= report_phase("ABSORB→ringA", phys_to_logical(ringa_phys()),
                           lanes)

    for r in range(24):
        C, D, tr, B, gold = round_trace(gold, r)
        for rt in range(100):
            tick(KECCAK_RC[r] if rt == 76 else 0)   # v2：RC 窗 75→76
            if probe and r == 0 and rt in (24, 50, 75, 99):
                if rt == 24:
                    phys = slots(sim.latch_values, cd_bus, 5)
                    cexp = [phys[(x + 1) % 5] for x in range(5)]
                    ok &= report_phase("R0 P1→cd", cexp, C)
                elif rt == 50:
                    ok &= report_phase("R0 P2→ringA",
                                       phys_to_logical(ringa_phys()), tr)
                elif rt == 75:
                    _, _, sub_b = subs()
                    phys = slots(sub_b.latch_values, stb_bus, 25)
                    ok &= report_phase("R0 P3→ringB", phys, B)
                else:
                    ok &= report_phase("R0 P4→ringA",
                                       phys_to_logical(ringa_phys()), gold)
        if probe and r == 0 and not ok:
            print("  ✗ 第 0 轮已偏离，停止（后续轮次无意义）")
            return None, False

    digest = []
    for i in range(5):                    # SQUEEZE 5 拍（stick 0..4）
        out = tick()
        if i >= 1:                        # stick 1..4 = 逻辑车道 0..3
            digest.append(sum(int(out[k]) << k for k in range(64)))
    out = tick()
    assert out[65], "done 未置位"
    return b"".join(w.to_bytes(8, "little") for w in digest), ok


def main():
    t0 = time.time()

    for msg in (b"\x00" * 64, MSG):
        lanes = keccak256_golden(msg)
        dig = b"".join(lanes[i].to_bytes(8, "little") for i in range(4))
        assert dig == engine_keccak256(msg), "黄金模型自检失败"
    print("✓ 黄金模型 == engine.keccak256（2 组）")

    comp, _ = build_keccak_component()
    ra_lo, pa_lo = build_kl_ringa_lo()
    ra_hi, pa_hi = build_kl_ringa_hi()
    rho, _ = build_kl_rho()
    rb, pb = build_kl_ringb()
    c, probes = build_kl_top((V2_CPU, KC_CID), (V2_CPU, RA_LO_CID),
                             (V2_CPU, RA_HI_CID), (V2_CPU, RB_CID),
                             (V2_CPU, RHO_CID))
    for name, cc in (("kl_ringa_lo", ra_lo), ("kl_ringa_hi", ra_hi),
                     ("kl_rho", rho), ("kl_ringb", rb), ("kl_top", c)):
        nb = len(cc.to_bytes())
        print(f"  {name}: {len(cc.instructions)} 指令 / "
              f"{cc.gate_count()} 门 / {nb} 字节 / "
              f"{cc.n_in} 入 {cc.n_out} 出"
              f"{'  ⚠️ 超 39KB 上限!' if nb > 39000 else ''}")
    probes["st_lo"] = pa_lo["st"]
    probes["st_hi"] = pa_hi["st"]
    probes["stb"] = pb["stb"]

    resolver = REFResolver()
    resolver.register(V2_CPU, KC_CID, comp)
    resolver.register(V2_CPU, RA_LO_CID, ra_lo)
    resolver.register(V2_CPU, RA_HI_CID, ra_hi)
    resolver.register(V2_CPU, RHO_CID, rho)
    resolver.register(V2_CPU, RB_CID, rb)
    sim = SimWithREF(c, resolver)

    print("  消息1（带逐阶段探针）...")
    got, ok = run_one(sim, probes, MSG, probe=True)
    assert ok, "探针阶段未全过"
    want = engine_keccak256(MSG)
    print(f"  摘要: {got.hex() if got else None}")
    print(f"  期望: {want.hex()}")
    assert got == want, "摘要不符"

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

    print(f"✓ kl_split 全绿（{time.time() - t0:.1f}s）")


if __name__ == "__main__":
    main()
