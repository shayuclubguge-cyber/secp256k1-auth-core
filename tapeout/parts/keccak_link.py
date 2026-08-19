"""
keccak_link —— 流式 Keccak-256 电路（Qx‖Qy 64 字节 → 32 字节摘要）

设计：docs/keccak_link_design.md（v3：双环乒乓，π 经第二环避开原地置换毁损）。
单电路四阶段轮转 × 24 轮（每轮 100 拍，整 25 的倍数，跨轮零偏移）：
  ABSORB(25) → 24 × [P1 θ累加(25) → P2 θ施加+ρ(25) → P3 π(25) → P4 χ+ι(25)]
  → SQUEEZE(4) → DONE

状态：ringA 1,600 位 rot64 环（θρ 与最终态）+ ringB 1,600 位环（π 暂存）
  + CD 环 320 位（θ 列奇偶）+ buf0/buf1（χ 行内回绕）。
共享部件：REF×2 → keccak 组件（χ/XOR3 双模式，v1 cid4 同网表）、
  6 级筒式移位器（ρ）、25 选 1 车道 mux（π，~3.8k NAND）。

阶段数据流（关键正确性论证）：
  P1：读 ringA 底片（车道 t），XOR3(st_s0, cd_s0&ge5, 0) → CD 顶装。
      拍 t 时 cd_s0 = 拍 t−5 装入的同列部分积 → 25 拍累出 C[0..4]。
      （CD 每拍旋转：拍 t 槽 s 存拍 t−5+s 装入的值）
  P2：CD 再循环（拍 t 槽 s = C[(s+t)%5]）→ C[x−1]恒在 s4、C[x+1]恒在 s1，
      REF1 = XOR3(cd_s4, rot1(cd_s1), 0) = D[x]（零 mux）；
      REF2 = XOR3(st_s0, D, 0) → ρ 筒式移位 → ringA 顶装。
      25 拍恒等对齐，ringA 槽 t = ρθA[t]。
  P3：ringA we=0 纯再循环（无毁损！）；pi_mux 以 σ(t)=(π⁻¹(t)−t)%25
      从 ringA 任意槽读车道 π⁻¹(t) → ringB 顶装。拍 t 写 → 末槽 t。
      ringB 槽 w = B[w]（π 完成，旧值坠落无害）。
  P4：ringB 再循环，底片 s0/s1/s2 + buf0/buf1（x=3,4 行回绕）送 χ；
      拍 0 叠 ι（in_word=RC[r] 门控 XOR）；结果写 ringA 顶。
      ringA 坠落的是 ρθA 车道——已全部读过，无害。末态 ringA 槽 t = A'[t]。
  每轮 100 拍 ≡ 0 (mod 25)：跨轮对齐零偏移。

引脚：65 入（in_word64/start）/ 66 出（out_word64/busy/done）。
流片 ~105KB、beat gas ~30M+：均走 MetaMask（OKX 24,575B 上限不够）。

无 4 拍网格纪律（独立电路）：驱动器按拍喂数并等 done。in_word 调度：
  ABSORB tick 0..7：输入 8 车道（小端）；tick 8：0x01；tick 9..15：0；
  tick 16：0x8000000000000000；tick 17..24：任意
  每轮 P4 首拍（轮内 tick 75）：RC[round]
"""

from typing import Dict, List, Tuple

from .. import Circuit, Signal

# keccak 组件（v1 cid4 / v2 补流片同网表）：204 入 64 出
KC_IN, KC_OUT = 204, 64

KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

ROT_OFFSETS = [  # [x][y]
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
# 按车道号 ℓ=x+5y 展平
RHO = [ROT_OFFSETS[l % 5][l // 5] % 64 for l in range(25)]

# π⁻¹(w)：B[w] = A[π⁻¹(w)]；x' = y, y' = (2x+3y)%5 的逆
PI_INV = []
for w in range(25):
    xp, yp = w % 5, w // 5          # B 坐标 (x', y')
    y = xp
    x = (3 * yp + xp) % 5
    PI_INV.append(x + 5 * y)
# P3(π) 拍 t 读 ringA 的槽号：σ(t) = (π⁻¹(t) − t) mod 25
SIGMA = [(PI_INV[t] - t) % 25 for t in range(25)]

# FSM
ST_IDLE, ST_ABS, ST_RND, ST_SQ, ST_DONE = 0, 1, 2, 3, 4
RP_P1, RP_P2, RP_P3, RP_P4 = 0, 1, 2, 3

# 轮内 tick 边界：P1 [0,25) P2 [25,50) P3(π) [50,75) P4(χ+ι) [75,100)
T_P2, T_P3, T_P4, T_END = 25, 50, 75, 100


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def _nand_mux4(c: Circuit, srcs: List[List[Signal]],
               sels: List[Signal]) -> List[Signal]:
    """one-hot 4 路 64 位 mux：NAND4 结构 out = ¬(Π ts_i) = OR(src_i·sel_i)"""
    out = []
    for k in range(64):
        ts = [c.nand(srcs[i][k], sels[i]) for i in range(4)]
        out.append(c.nand(c.and_(ts[0], ts[1]), c.and_(ts[2], ts[3])))
    return out


def _eq_const(c: Circuit, bits: List[Signal], v: int) -> Signal:
    t = c.one()
    for i, b in enumerate(bits):
        t = c.and_(t, b if (v >> i) & 1 else c.not_(b))
    return t


class _Counter:
    """使能递增计数器（自然回绕）；clr 优先"""

    def __init__(self, c: Circuit, n: int):
        self.bits = c.latch_bus(n)
        self.n = n
        self.c = c

    def drive(self, inc_en: Signal, clr: Signal):
        c = self.c
        cry = c.one()
        clr_n = c.not_(clr)
        inc_en_n = c.not_(inc_en)
        d = []
        for i in range(self.n):
            inc_i = c.xor(self.bits[i], cry)
            cry = c.and_(self.bits[i], cry)
            t = _mux3(c, self.bits[i], inc_i, inc_en, inc_en_n)
            d.append(c.and_(t, clr_n))
        c.drive_bus(self.bits, d)


def build_keccak_link(child_cpu: bytes, comp_cid: int) -> Tuple[Circuit, Dict]:
    """
    构造 keccak_link。child_cpu/comp_cid 指向已流片的 keccak 组件
    （204 入 64 出，χ/XOR3 双模式）。
    返回 (circuit, probes)：probes 含内部 LATCH 信号（测试用）。
    """
    c = Circuit("keccak_link", n_in=65, n_out=66)
    pins = c.input_signals()
    in_word = pins[0:64]
    start = pins[64]
    Z = c.zero()
    ONE = c.one()

    # ---------------- 状态 ----------------
    st = c.latch_bus(1600)          # ringA：θρ 与轮末态
    stb = c.latch_bus(1600)         # ringB：π 暂存（B 矩阵）
    cd = c.latch_bus(320)           # CD 环：θ 列奇偶 C[0..4]
    buf0 = c.latch_bus(64)
    buf1 = c.latch_bus(64)
    st2 = c.latch_bus(3)            # 主 FSM
    rp = c.latch_bus(2)             # 轮内阶段
    atick = _Counter(c, 5)          # ABSORB tick
    rtick = _Counter(c, 7)          # 轮内 tick 0..99
    rnd = _Counter(c, 5)            # 轮号 0..23
    p4t = _Counter(c, 5)            # P4(χ) 内 tick 0..24
    x3 = _Counter(c, 3)             # P4 内车道 x
    w5 = _Counter(c, 5)             # P3(π) 内 tick 0..24
    l5 = _Counter(c, 5)             # P2 车道号 0..24
    stick = _Counter(c, 2)          # SQUEEZE tick

    # ---------------- 译码 ----------------
    is_idle = _eq_const(c, st2, ST_IDLE)
    is_abs = _eq_const(c, st2, ST_ABS)
    is_rnd = _eq_const(c, st2, ST_RND)
    is_sq = _eq_const(c, st2, ST_SQ)
    is_done = _eq_const(c, st2, ST_DONE)

    rp_p1 = _eq_const(c, rp, RP_P1)
    rp_p2 = _eq_const(c, rp, RP_P2)
    rp_p3 = _eq_const(c, rp, RP_P3)
    rp_p4 = _eq_const(c, rp, RP_P4)
    is_p1 = c.and_(is_rnd, rp_p1)
    is_p2 = c.and_(is_rnd, rp_p2)
    is_p3 = c.and_(is_rnd, rp_p3)
    is_p4 = c.and_(is_rnd, rp_p4)

    eq24_a = _eq_const(c, atick.bits, 24)
    eq24_r = _eq_const(c, rtick.bits, T_P2 - 1)     # 24
    eq49_r = _eq_const(c, rtick.bits, T_P3 - 1)     # 49
    eq74_r = _eq_const(c, rtick.bits, T_P4 - 1)     # 74
    eq99_r = _eq_const(c, rtick.bits, T_END - 1)    # 99
    eq23_rnd = _eq_const(c, rnd.bits, 23)
    eq3_s = _eq_const(c, stick.bits, 3)
    # ABSORB 写窗：atick ≤ 16
    a = atick.bits
    lt17 = c.or_(c.not_(a[4]),
                 c.and_(c.and_(c.not_(a[3]), c.not_(a[2])),
                        c.and_(c.not_(a[1]), c.not_(a[0]))))
    # P1 累加窗：rtick ≥ 5
    r = rtick.bits
    ge5 = c.or_(c.or_(r[4], r[3]), c.and_(r[2], c.or_(r[1], r[0])))
    # ι 窗：p4t == 0
    p4_eq0 = _eq_const(c, p4t.bits, 0)
    x_eq0 = _eq_const(c, x3.bits, 0)
    x_eq1 = _eq_const(c, x3.bits, 1)
    x_eq3 = _eq_const(c, x3.bits, 3)
    x_eq4 = _eq_const(c, x3.bits, 4)

    # ---------------- FSM 迁移 ----------------
    rnd_end = c.and_(is_rnd, c.and_(rp_p4, eq99_r))
    st2_d_en = {
        ST_IDLE: c.and_(is_idle, c.not_(start)),
        ST_ABS: c.or_(c.and_(c.or_(is_idle, is_done), start),
                      c.and_(is_abs, c.not_(eq24_a))),
        ST_RND: c.or_(c.and_(is_abs, eq24_a),
                      c.and_(is_rnd, c.not_(c.and_(rnd_end, eq23_rnd)))),
        ST_SQ: c.or_(c.and_(rnd_end, eq23_rnd),
                     c.and_(is_sq, c.not_(eq3_s))),
        ST_DONE: c.or_(c.and_(is_sq, eq3_s),
                       c.and_(is_done, c.not_(start))),
    }
    st2_d = []
    for i in range(3):
        t = Z
        for k, en in st2_d_en.items():
            if (k >> i) & 1:
                t = c.or_(t, en)
        st2_d.append(t)
    c.drive_bus(st2, st2_d)

    # rp 迁移
    rp_en = {
        RP_P1: c.or_(c.and_(rp_p1, c.not_(eq24_r)),
                     c.and_(rp_p4, eq99_r)),
        RP_P2: c.or_(c.and_(rp_p1, eq24_r),
                     c.and_(rp_p2, c.not_(eq49_r))),
        RP_P3: c.or_(c.and_(rp_p2, eq49_r),
                     c.and_(rp_p3, c.not_(eq74_r))),
        RP_P4: c.or_(c.and_(rp_p3, eq74_r),
                     c.and_(rp_p4, c.not_(eq99_r))),
    }
    rp_d = []
    for i in range(2):
        t = Z
        for k, en in rp_en.items():
            if (k >> i) & 1:
                t = c.or_(t, en)
        rp_d.append(c.and_(t, is_rnd))
    c.drive_bus(rp, rp_d)

    # ---------------- 计数器 ----------------
    atick.drive(c.and_(is_abs, c.not_(eq24_a)), c.not_(is_abs))
    rtick.drive(c.and_(is_rnd, c.not_(rnd_end)),
                c.or_(c.not_(is_rnd), rnd_end))
    rnd.drive(c.and_(rnd_end, c.not_(eq23_rnd)), c.not_(is_rnd))
    p4t.drive(is_p4, c.not_(is_p4))
    x3.drive(c.and_(is_p4, c.not_(x_eq4)),
             c.or_(c.not_(is_p4), c.and_(is_p4, x_eq4)))
    w5.drive(is_p3, c.not_(is_p3))
    l5.drive(is_p2, c.not_(is_p2))
    stick.drive(is_sq, c.not_(is_sq))

    # ---------------- REF1：D[x] = C[x−1] ^ rot1(C[x+1]) ----------------
    # P2 拍 t 处理车道 t（x=t%5）；CD 每拍再循环，拍 t 槽 s 存 C[(s+t)%5]
    # → C[x−1] 恒在 s4，C[x+1] 恒在 s1（零 mux）
    cd_s0 = cd[0:64]
    cd_s1 = cd[64:128]
    cd_s4 = cd[256:320]
    rot1_cd1 = [cd_s1[(k - 1) % 64] for k in range(64)]
    ref1_in = cd_s4 + rot1_cd1 + [Z] * 64 \
        + [Z] * 5 + [Z] * 5 + [ONE, Z]           # mode=01 XOR3
    ref1 = c.ref(child_cpu, comp_cid, KC_IN, KC_OUT, ref1_in)
    d_word = ref1[0:64]

    # ---------------- REF2 端口 ----------------
    st_s0 = st[0:64]
    stb_s0 = stb[0:64]
    stb_s1 = stb[64:128]
    stb_s2 = stb[128:192]
    # a：P4(χ)→ringB 底片；其余→ringA 底片
    a_word = _nand_mux4(c, [stb_s0, st_s0, st_s0, st_s0],
                        [is_p4, is_abs, is_p1, is_p2])
    # b：absorb→in_word；P1→cd_s0&ge5（拍 t 时 s0=拍 t−5 的同列部分积）；
    #    P2→D；P4(χ)→(x==4 ? buf0 : stb_s1)
    cd_s0_g = [c.and_(cd_s0[k], ge5) for k in range(64)]
    x_eq4_n = c.not_(x_eq4)
    p4b = [_mux3(c, stb_s1[k], buf0[k], x_eq4, x_eq4_n) for k in range(64)]
    b_word = _nand_mux4(c, [in_word, cd_s0_g, d_word, p4b],
                        [is_abs, is_p1, is_p2, is_p4])
    # c：P4(χ)→(x==3 ? buf0 : x==4 ? buf1 : stb_s2)，其余 0
    x_eq3_n = c.not_(x_eq3)
    c_p4 = []
    for k in range(64):
        t = _mux3(c, stb_s2[k], buf0[k], x_eq3, x_eq3_n)
        t = _mux3(c, t, buf1[k], x_eq4, x_eq4_n)
        c_p4.append(c.and_(t, is_p4))
    ref2_in = a_word + b_word + c_p4 \
        + [Z] * 5 + [Z] * 5 + [c.not_(is_p4), Z]   # mode: P4=χ(00) 否则 XOR3(01)
    ref2 = c.ref(child_cpu, comp_cid, KC_IN, KC_OUT, ref2_in)
    ref2_out = ref2[0:64]

    # ---------------- ρ 筒式移位器（6 级） ----------------
    rho_bits = []
    for j in range(6):
        t = Z
        for lane in range(25):
            if (RHO[lane] >> j) & 1:
                t = c.or_(t, _eq_const(c, l5.bits, lane))
        rho_bits.append(t)
    bar = ref2_out
    for stage in range(6):
        sh = 1 << stage
        rot = [bar[(k - sh) % 64] for k in range(64)]      # rotl(sh)
        sel = rho_bits[stage]
        sel_n = c.not_(sel)
        bar = [_mux3(c, bar[k], rot[k], sel, sel_n) for k in range(64)]

    # ---------------- π：25 选 1 车道 mux（读 ringA 任意槽） ----------------
    # P3 拍 t：ringA 已净转 t 拍 → 车道 L 在槽 (L−t)%25；σ(t) 选中 π⁻¹(t)
    sig_bits = []
    for j in range(5):
        t = Z
        for w_ in range(25):
            if (SIGMA[w_] >> j) & 1:
                t = c.or_(t, _eq_const(c, w5.bits, w_))
        sig_bits.append(t)
    sig_eq = [_eq_const(c, sig_bits, s) for s in range(25)]
    pi_mux = []
    for k in range(64):
        acc = ONE
        for s in range(25):
            acc = c.and_(acc, c.nand(st[64 * s + k], sig_eq[s]))
        pi_mux.append(c.not_(acc))

    # ---------------- 写回 ----------------
    # ι：P4 首拍把 RC（=in_word）XOR 进 χ 输出
    rc_gate = c.and_(is_p4, p4_eq0)
    chi_iota = [c.xor(ref2_out[k], c.and_(in_word[k], rc_gate))
                for k in range(64)]
    # ringA 顶片数据：abs→REF2out；P2→barrel；P4→χ+ι
    wdata = _nand_mux4(c, [ref2_out, bar, chi_iota, ref2_out],
                       [is_abs, is_p2, is_p4, Z])
    we_state = c.or_(c.and_(is_abs, lt17), c.or_(is_p2, is_p4))
    we_state_n = c.not_(we_state)
    st_d: List[Signal] = [st[64 + k] for k in range(1536)]
    st_d += [_mux3(c, st[j], wdata[j], we_state, we_state_n)
             for j in range(64)]
    c.drive_bus(st, st_d)

    # ringB：P3 顶装 pi_mux，其余再循环（坠落物无害）
    we_b_n = c.not_(is_p3)
    stb_d: List[Signal] = [stb[64 + k] for k in range(1536)]
    stb_d += [_mux3(c, stb[j], pi_mux[j], is_p3, we_b_n)
              for j in range(64)]
    c.drive_bus(stb, stb_d)

    # CD：P1 顶装 REF2out（其余拍再循环，P2 供 D 抽头）
    cd_d: List[Signal] = [cd[64 + k] for k in range(256)]
    cd_d += [_mux3(c, cd[j], ref2_out[j], is_p1, c.not_(is_p1))
             for j in range(64)]
    c.drive_bus(cd, cd_d)

    # buf0/buf1：P4(χ) 内 x==0 / x==1 拍捕获 ringB 底片（行回绕用）
    ld0 = c.and_(is_p4, x_eq0)
    ld1 = c.and_(is_p4, x_eq1)
    c.drive_bus(buf0, [_mux3(c, buf0[k], stb_s0[k], ld0, c.not_(ld0))
                       for k in range(64)])
    c.drive_bus(buf1, [_mux3(c, buf1[k], stb_s0[k], ld1, c.not_(ld1))
                       for k in range(64)])

    # ---------------- 输出 ----------------
    out_word = [c.and_(st_s0[k], is_sq) for k in range(64)]
    busy = c.not_(c.or_(is_idle, is_done))
    c.seal_outputs(out_word + [busy, is_done])

    probes = dict(st=st, stb=stb, cd=cd, rp=rp, rtick=rtick.bits,
                  rnd=rnd.bits)
    return c, probes


# ---------------------------------------------------------------------------
# 黄金参考（车道级，与 tests/test_keccak.py 参考实现同构）
# ---------------------------------------------------------------------------

def rotl64(v: int, r: int) -> int:
    r %= 64
    return ((v << r) | (v >> (64 - r))) & ((1 << 64) - 1) if r else v


def keccak_f_lanes(lanes: List[int], round_idx: int) -> List[int]:
    """单轮黄金：θ → ρπ → χ → ι（车道列表，ℓ=x+5y）"""
    M = (1 << 64) - 1
    C = [0] * 5
    for x in range(5):
        for y in range(5):
            C[x] ^= lanes[x + 5 * y]
    D = [C[(x - 1) % 5] ^ rotl64(C[(x + 1) % 5], 1) for x in range(5)]
    for x in range(5):
        for y in range(5):
            lanes[x + 5 * y] ^= D[x]
    B = [0] * 25
    for x in range(5):
        for y in range(5):
            B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl64(lanes[x + 5 * y],
                                                      ROT_OFFSETS[x][y])
    for x in range(5):
        for y in range(5):
            lanes[x + 5 * y] = B[x + 5 * y] ^ (
                (~B[(x + 1) % 5 + 5 * y]) & B[(x + 2) % 5 + 5 * y]) & M
    lanes[0] ^= KECCAK_RC[round_idx]
    return lanes


def keccak256_golden(data64: bytes) -> List[int]:
    """64 字节输入 → 24 轮 → 返回 25 车道（摘要 = 车道 0..3）"""
    assert len(data64) == 64
    lanes = [0] * 25
    for i in range(8):
        lanes[i] = int.from_bytes(data64[8 * i:8 * i + 8], "little")
    lanes[8] ^= 0x01
    lanes[16] ^= 0x8000000000000000
    for r in range(24):
        lanes = keccak_f_lanes(lanes, r)
    return lanes
