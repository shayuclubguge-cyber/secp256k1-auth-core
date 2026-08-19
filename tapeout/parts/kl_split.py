"""
keccak_link 拆分版（BEP-652 适配）—— 5 颗 REF 链式零件

背景：2026-04-28 Mendel 硬分叉（BEP-652 / EIP-7825）后，BSC 单交易 gas
上限永久锁死 16,777,216（实测合约 gas ≈ 421 × 网表字节 → 单电路
≤ ~39KB）。原 110KB 单电路 keccak_link（~47M gas）一笔永远无法上链，
合约亦无分片上传接口（已核实官方前端只调 tapeout 单次）。拆为：

  kl_ringa_lo 38→64   ringA 低 32 位片（25 槽 × 32b = 800L）+ π 半 mux
  kl_ringa_hi 38→64   ringA 高 32 位片（800L）+ π 半 mux
  kl_ringb    75→192  ringB 1600L + buf0/buf1 + b20/b21 保持 + χ 操作数装配
  kl_rho      69→64   ρ 筒式移位（6 级 barrel + 车道译码，纯组合）
  kl_top      65→66   FSM/计数器/CD 环/w_hold/pi_hold + REF×7

为什么这样拆：
- REF 是"输入就绪才求值"的子程序调用；同拍 read-modify-write 跨 REF
  边界会构成信号环 → 所有环写改为保持寄存器流水（算好存一拍、下拍写）。
- 环按**槽**拆两半会切断回绕边（两个方向的跨界信号互为输入输出，
  无法排序求值）；按**位片**拆则两半各自独立回绕，零跨界、零冗余状态。
- 采用「物理槽 = 逻辑车道 + 1 (mod 25)」全局恒定约定（每轮自洽复原）：
  ABSORB 拍 t 算 → w_hold，拍 t+1 写（车道 t 落槽 t+1）
  P1      拍 τ 底片 = 逻辑 (τ−1)；CD[s]=C[s−1] → D 抽头 s4/s1 不变
  P2      拍 t 处理逻辑 (t−1)：REF2→ρ→w_hold，拍 t+1 写（末写 rtick 50）
  P3      拍 t 读槽 (σ(t)+2) mod 25 → pi_hold；rtick 51..75 写 ringB
  P4      拍 u 处理逻辑 (u−1)（顺序 24,0,…,23）：u=0 用 pi_hold/b20/b21；
          x3 译码恒 −1 重映射；ι 落于 u=1（rtick 76）
  SQUEEZE 5 拍（stick 1..4 流出逻辑车道 0..3）

外部协议仅两处微调（纯驱动侧）：RC 窗 rtick 75→76；SQUEEZE 4→5 拍。
轮长仍 100 拍 ≡ 0 (mod 25)，跨轮零偏移。
"""

from typing import Dict, List, Tuple

from .. import Circuit, Signal
from .keccak_link import (
    KC_IN, KC_OUT, KECCAK_RC, RHO, SIGMA,
    _Counter, _eq_const, _mux3, _nand_mux4,
)

# P3 读槽调度：物理槽 = (σ(w)+2) mod 25（注意 σ 非置换，必须按值译码）
SIGMA2 = [(s + 2) % 25 for s in SIGMA]


def _build_ringa_half(name: str, hi: bool) -> Tuple[Circuit, Dict]:
    """ringA 的 32 位位片。in: din32|we|w5[5]；out: s0[32]|pi[32]"""
    lo_s, hi_s = (16, 25) if hi else (0, 16)  # 占位，实际按 32 位切
    c = Circuit(name, n_in=38, n_out=64)
    pins = c.input_signals()
    din = pins[0:32]
    we = pins[32]
    w5b = pins[33:38]
    ONE = c.one()

    st = c.latch_bus(800)           # 25 槽 × 32 位

    # w5 → eq → σ' 槽位 one-hot（σ' 非置换：同槽多拍取 OR）
    eq_w = [_eq_const(c, w5b, w) for w in range(25)]
    sig_eq = []
    for s in range(25):
        t = None
        for w in range(25):
            if SIGMA2[w] == s:
                t = eq_w[w] if t is None else c.or_(t, eq_w[w])
        sig_eq.append(t if t is not None else c.zero())

    pi_mux = []
    for k in range(32):
        acc = c.nand(st[k], sig_eq[0])
        for s in range(1, 25):
            acc = c.and_(acc, c.nand(st[32 * s + k], sig_eq[s]))
        pi_mux.append(c.not_(acc))

    we_n = c.not_(we)
    st_d: List[Signal] = [st[32 + k] for k in range(768)]
    st_d += [_mux3(c, st[j], din[j], we, we_n) for j in range(32)]
    c.drive_bus(st, st_d)

    c.seal_outputs(st[0:32] + pi_mux)
    return c, dict(st=st)


def build_kl_ringa_lo() -> Tuple[Circuit, Dict]:
    return _build_ringa_half("kl_ringa_lo", hi=False)


def build_kl_ringa_hi() -> Tuple[Circuit, Dict]:
    return _build_ringa_half("kl_ringa_hi", hi=True)


def build_kl_rho() -> Tuple[Circuit, Dict]:
    """ρ 筒式移位。in: data64|lane[5]；out: rotl(data, RHO[lane−1]) 64
    （逻辑车道 = 输入车道号 −1 的重映射在译码表里完成）"""
    c = Circuit("kl_rho", n_in=69, n_out=64)
    pins = c.input_signals()
    data = pins[0:64]
    lane_b = pins[64:69]
    Z = c.zero()

    eq_l = [_eq_const(c, lane_b, L) for L in range(25)]
    rho_bits = []
    for j in range(6):
        t = Z
        for lane in range(25):
            if (RHO[lane] >> j) & 1:
                t = c.or_(t, eq_l[(lane + 1) % 25])
        rho_bits.append(t)
    bar = data
    for stage in range(6):
        sh = 1 << stage
        rot = [bar[(k - sh) % 64] for k in range(64)]
        sel = rho_bits[stage]
        sel_n = c.not_(sel)
        bar = [_mux3(c, bar[k], rot[k], sel, sel_n) for k in range(64)]
    c.seal_outputs(bar)
    return c, {}


def build_kl_ringb() -> Tuple[Circuit, Dict]:
    """ringB 环 + χ 操作数装配。
    in:  wb64|we_b|p4eq0|is_p3|w5[5]|x3[3]  (75)
    out: a_chi|b_chi|c_chi                 (192)
    """
    c = Circuit("kl_ringb", n_in=75, n_out=192)
    pins = c.input_signals()
    wb = pins[0:64]
    we_b = pins[64]
    p4eq0 = pins[65]
    is_p3 = pins[66]
    w5b = pins[67:72]
    x3b = pins[72:75]

    stb = c.latch_bus(1600)
    buf0 = c.latch_bus(64)
    buf1 = c.latch_bus(64)
    b20 = c.latch_bus(64)
    b21 = c.latch_bus(64)
    s0, s1, s2 = stb[0:64], stb[64:128], stb[128:192]

    w5eq21 = _eq_const(c, w5b, 21)
    w5eq22 = _eq_const(c, w5b, 22)
    x3eq0 = _eq_const(c, x3b, 0)
    x3eq1 = _eq_const(c, x3b, 1)
    x3eq2 = _eq_const(c, x3b, 2)
    x3eq4 = _eq_const(c, x3b, 4)
    p4eq0_n = c.not_(p4eq0)
    x3eq0_n = c.not_(x3eq0)
    x3eq4_n = c.not_(x3eq4)
    x3eq1_n = c.not_(x3eq1)
    x3eq2_n = c.not_(x3eq2)

    # χ 操作数（逻辑车道 = x3−1 重映射；p4eq0 拍 = 逻辑 24，用保持寄存器）
    a_chi = [_mux3(c, s0[k], wb[k], p4eq0, p4eq0_n) for k in range(64)]
    b_chi = []
    c_chi = []
    for k in range(64):
        t = _mux3(c, s1[k], buf0[k], x3eq0, x3eq0_n)
        b_chi.append(_mux3(c, t, b20[k], p4eq0, p4eq0_n))
        t2 = _mux3(c, s2[k], buf1[k], x3eq0, x3eq0_n)
        t2 = _mux3(c, t2, buf0[k], x3eq4, x3eq4_n)
        c_chi.append(_mux3(c, t2, b21[k], p4eq0, p4eq0_n))

    we_b_n = c.not_(we_b)
    stb_d: List[Signal] = [stb[64 + k] for k in range(1536)]
    stb_d += [_mux3(c, stb[j], wb[j], we_b, we_b_n) for j in range(64)]
    c.drive_bus(stb, stb_d)

    c.drive_bus(buf0, [_mux3(c, buf0[k], s0[k], x3eq1, x3eq1_n)
                       for k in range(64)])
    c.drive_bus(buf1, [_mux3(c, buf1[k], s0[k], x3eq2, x3eq2_n)
                       for k in range(64)])
    # wb 在 P3 拍 t 携带的是 pi_hold = B[t−1]（保持寄存器流水），
    # 故 b20/b21 须晚一拍采样：t=21 捕 B[20]，t=22 捕 B[21]
    ld20 = c.and_(is_p3, w5eq21)
    ld21 = c.and_(is_p3, w5eq22)
    c.drive_bus(b20, [_mux3(c, b20[k], wb[k], ld20, c.not_(ld20))
                      for k in range(64)])
    c.drive_bus(b21, [_mux3(c, b21[k], wb[k], ld21, c.not_(ld21))
                      for k in range(64)])

    c.seal_outputs(a_chi + b_chi + c_chi)
    return c, dict(stb=stb, buf0=buf0, buf1=buf1)


def build_kl_top(comp: Tuple[bytes, int], ra_lo: Tuple[bytes, int],
                 ra_hi: Tuple[bytes, int], ringb: Tuple[bytes, int],
                 rho: Tuple[bytes, int]) -> Tuple[Circuit, Dict]:
    """keccak_link 主控（对外仍 65→66，协议见模块 docstring）"""
    c = Circuit("keccak_link", n_in=65, n_out=66)
    pins = c.input_signals()
    in_word = pins[0:64]
    start = pins[64]
    Z = c.zero()
    ONE = c.one()

    # ---------------- 状态（父级本地） ----------------
    cd = c.latch_bus(320)
    w_hold = c.latch_bus(64)
    pi_hold = c.latch_bus(64)
    st2 = c.latch_bus(3)
    rp = c.latch_bus(2)
    atick = _Counter(c, 5)
    rtick = _Counter(c, 7)
    rnd = _Counter(c, 5)
    p4t = _Counter(c, 5)
    x3 = _Counter(c, 3)
    w5 = _Counter(c, 5)
    l5 = _Counter(c, 5)
    stick = _Counter(c, 3)

    # ---------------- 译码 ----------------
    ST_IDLE, ST_ABS, ST_RND, ST_SQ, ST_DONE = 0, 1, 2, 3, 4
    RP_P1, RP_P2, RP_P3, RP_P4 = 0, 1, 2, 3
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
    eq24_r = _eq_const(c, rtick.bits, 24)
    eq49_r = _eq_const(c, rtick.bits, 49)
    eq74_r = _eq_const(c, rtick.bits, 74)
    eq99_r = _eq_const(c, rtick.bits, 99)
    eq25_r = _eq_const(c, rtick.bits, 25)
    eq50_r = _eq_const(c, rtick.bits, 50)
    eq23_rnd = _eq_const(c, rnd.bits, 23)
    eq4_s = _eq_const(c, stick.bits, 4)
    sq_ge1 = c.not_(_eq_const(c, stick.bits, 0))
    a = atick.bits
    lt17 = c.or_(c.not_(a[4]),
                 c.and_(c.and_(c.not_(a[3]), c.not_(a[2])),
                        c.and_(c.not_(a[1]), c.not_(a[0]))))   # atick ≤ 16
    le17_a = c.not_(c.and_(a[4], c.or_(a[3], c.or_(a[2], a[1]))))  # ≤ 17
    ge1_a = c.not_(_eq_const(c, a, 0))
    r = rtick.bits
    ge5 = c.or_(c.or_(r[4], r[3]), c.and_(r[2], c.or_(r[1], r[0])))
    w5eq0 = _eq_const(c, w5.bits, 0)
    p4_eq0 = _eq_const(c, p4t.bits, 0)
    p4_eq1 = _eq_const(c, p4t.bits, 1)
    rc_gate = c.and_(is_p4, p4_eq1)     # ι：逻辑车道 0 在 P4 拍 1

    # ---------------- FSM（v1，仅 SQUEEZE 4→5 拍） ----------------
    rnd_end = c.and_(is_rnd, c.and_(rp_p4, eq99_r))
    st2_d_en = {
        ST_IDLE: c.and_(is_idle, c.not_(start)),
        ST_ABS: c.or_(c.and_(c.or_(is_idle, is_done), start),
                      c.and_(is_abs, c.not_(eq24_a))),
        ST_RND: c.or_(c.and_(is_abs, eq24_a),
                      c.and_(is_rnd, c.not_(c.and_(rnd_end, eq23_rnd)))),
        ST_SQ: c.or_(c.and_(rnd_end, eq23_rnd),
                     c.and_(is_sq, c.not_(eq4_s))),
        ST_DONE: c.or_(c.and_(is_sq, eq4_s),
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

    # ---------------- 计数器（v1） ----------------
    x_eq4 = _eq_const(c, x3.bits, 4)
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

    # -------- REF 链：ringb → χ → 位片环 → θD/XOR3 → ρ --------
    we_b = c.or_(c.and_(is_p3, c.not_(w5eq0)), c.and_(is_p4, p4_eq0))
    refb = c.ref(ringb[0], ringb[1], 75, 192,
                 pi_hold + [we_b, p4_eq0, is_p3] + w5.bits + x3.bits)
    a_chi, b_chi, c_chi = refb[0:64], refb[64:128], refb[128:192]

    ref3 = c.ref(comp[0], comp[1], KC_IN, KC_OUT,
                 a_chi + b_chi + c_chi + [Z] * 10 + [Z, Z])   # mode 00 = χ
    chi_iota = [c.xor(ref3[k], c.and_(in_word[k], rc_gate))
                for k in range(64)]

    # ringA 写口：P4 → χ+ι（同拍直写）；其余 → w_hold
    is_p4_n = c.not_(is_p4)
    din = [_mux3(c, w_hold[k], chi_iota[k], is_p4, is_p4_n)
           for k in range(64)]
    we_abs = c.and_(is_abs, c.and_(ge1_a, le17_a))         # atick 1..17
    we_rho = c.or_(c.and_(is_p2, c.not_(eq25_r)), eq50_r)  # rtick 26..50
    we_ringa = c.or_(c.or_(we_abs, we_rho), is_p4)
    reflo = c.ref(ra_lo[0], ra_lo[1], 38, 64,
                  din[0:32] + [we_ringa] + w5.bits)
    refhi = c.ref(ra_hi[0], ra_hi[1], 38, 64,
                  din[32:64] + [we_ringa] + w5.bits)
    st_s0 = reflo[0:32] + refhi[0:32]
    pi = reflo[32:64] + refhi[32:64]

    # REF1：D[x] = C[x−1] ^ rot1(C[x+1])
    cd_s0 = cd[0:64]
    cd_s1 = cd[64:128]
    cd_s4 = cd[256:320]
    rot1_cd1 = [cd_s1[(k - 1) % 64] for k in range(64)]
    ref1 = c.ref(comp[0], comp[1], KC_IN, KC_OUT,
                 cd_s4 + rot1_cd1 + [Z] * 64 + [Z] * 10 + [ONE, Z])
    d_word = ref1[0:64]

    # REF2（XOR3 专用）：abs→(s0,in_word) / P1→(s0,cd_s0&ge5) / P2→(s0,D)
    cd_s0_g = [c.and_(cd_s0[k], ge5) for k in range(64)]
    b_word = _nand_mux4(c, [in_word, cd_s0_g, d_word, d_word],
                        [is_abs, is_p1, is_p2, Z])
    ref2 = c.ref(comp[0], comp[1], KC_IN, KC_OUT,
                 st_s0 + b_word + [Z] * 64 + [Z] * 10 + [ONE, Z])
    ref2_out = ref2[0:64]

    # ρ（子电路；逻辑车道 = l5−1 的重映射在 kl_rho 译码表内）
    refr = c.ref(rho[0], rho[1], 69, 64, ref2_out + l5.bits)
    bar = refr[0:64]

    # ---------------- 保持寄存器与 CD 写 ----------------
    en_wh = c.or_(c.and_(is_abs, lt17), is_p2)
    is_p2_n = c.not_(is_p2)
    wh_src = [_mux3(c, ref2_out[k], bar[k], is_p2, is_p2_n)
              for k in range(64)]
    en_wh_n = c.not_(en_wh)
    c.drive_bus(w_hold, [_mux3(c, w_hold[k], wh_src[k], en_wh, en_wh_n)
                         for k in range(64)])
    is_p3_n = c.not_(is_p3)
    c.drive_bus(pi_hold, [_mux3(c, pi_hold[k], pi[k], is_p3, is_p3_n)
                          for k in range(64)])

    is_p1_n = c.not_(is_p1)
    cd_d: List[Signal] = [cd[64 + k] for k in range(256)]
    cd_d += [_mux3(c, cd[j], ref2_out[j], is_p1, is_p1_n)
             for j in range(64)]
    c.drive_bus(cd, cd_d)

    # ---------------- 输出 ----------------
    out_gate = c.and_(is_sq, sq_ge1)
    out_word = [c.and_(st_s0[k], out_gate) for k in range(64)]
    busy = c.not_(c.or_(is_idle, is_done))
    c.seal_outputs(out_word + [busy, is_done])

    probes = dict(cd=cd, rp=rp, rtick=rtick.bits, rnd=rnd.bits)
    return c, probes
