"""
ecrecover_driver —— 在 fadd64 数据通路上跑完整 ECRECOVER

结构逐行镜像 engine.py 黄金模型（相同公式、相同调度），区别 only：
所有 256 位模运算都流经 fadd64 ACC（op 流可复放、可上链 beat 见证）。
点坐标等中间值由驱动器持有（链下），与 modmul 的操作数翻倍同理。

拍数预算（实测）：mul ≈ 550 拍，inv ≈ 393k 拍（510 次乘），
完整 ecrecover ≈ 2×scalar_mul(3,072 乘) + lift_x + 2×inv + 收尾 ≈ 4~5M 拍，
本地仿真 ~15-20 分钟。
"""

from typing import Optional, Tuple

from ..field import P, N
from ..engine import keccak256, G, INF, GX, GY
from .alu_driver import AluDriver, RC_P, RC_N

EXP_P_MINUS_2 = P - 2
EXP_SQRT = (P + 1) // 4
EXP_N_MINUS_2 = N - 2


def powmod_via_driver(d: AluDriver, base: int, e: int, m: int, rc: int) -> int:
    """平方-乘调度（与 engine.hw_invmod 同构）"""
    acc = 1 % m
    base %= m
    for i in range(256):
        if (e >> i) & 1:
            acc = d.mulmod(acc, base, m, rc)
        base = d.mulmod(base, base, m, rc)
    return acc


def invmod_p(d: AluDriver, a: int) -> int:
    return powmod_via_driver(d, a, EXP_P_MINUS_2, P, RC_P)


def invmod_n(d: AluDriver, a: int) -> int:
    return powmod_via_driver(d, a, EXP_N_MINUS_2, N, RC_N)


def sqrt_p(d: AluDriver, y2: int) -> int:
    return powmod_via_driver(d, y2, EXP_SQRT, P, RC_P)


# ---------------------------------------------------------------------------
# Jacobian 点运算（公式与 engine.py 完全一致）
# ---------------------------------------------------------------------------

def point_double(d: AluDriver, Pt):
    X, Y, Z = Pt
    if Z == 0 or Y == 0:
        return INF
    XX = d.mulmod_p(X, X)
    YY = d.mulmod_p(Y, Y)
    YYYY = d.mulmod_p(YY, YY)
    ZZ = d.mulmod_p(Z, Z)
    t = d.addmod_p(X, YY)
    t = d.mulmod_p(t, t)
    t = d.submod_p(t, XX)
    t = d.submod_p(t, YYYY)
    S = d.mulmod_p(2, t)
    M = d.mulmod_p(3, XX)
    T = d.submod_p(d.mulmod_p(M, M), d.mulmod_p(2, S))
    Y3 = d.submod_p(d.mulmod_p(M, d.submod_p(S, T)), d.mulmod_p(8, YYYY))
    Z3 = d.mulmod_p(2, d.mulmod_p(Y, Z))
    return (T, Y3, Z3)


def point_add(d: AluDriver, P1, P2):
    """与 engine.point_add 相同公式"""
    X1, Y1, Z1 = P1
    X2, Y2, Z2 = P2
    if Z1 == 0:
        return P2
    if Z2 == 0:
        return P1
    Z1Z1 = d.mulmod_p(Z1, Z1)
    Z2Z2 = d.mulmod_p(Z2, Z2)
    U1 = d.mulmod_p(X1, Z2Z2)
    U2 = d.mulmod_p(X2, Z1Z1)
    S1 = d.mulmod_p(d.mulmod_p(Y1, Z2), Z2Z2)
    S2 = d.mulmod_p(d.mulmod_p(Y2, Z1), Z1Z1)
    if U1 == U2:
        if S1 != S2:
            return INF
        return point_double(d, P1)
    H = d.submod_p(U2, U1)
    R = d.submod_p(S2, S1)
    H2 = d.mulmod_p(H, H)
    H3 = d.mulmod_p(H2, H)
    U1H2 = d.mulmod_p(U1, H2)
    X3 = d.submod_p(d.submod_p(d.mulmod_p(R, R), H3), d.mulmod_p(2, U1H2))
    Y3 = d.submod_p(d.mulmod_p(R, d.submod_p(U1H2, X3)), d.mulmod_p(S1, H3))
    Z3 = d.mulmod_p(d.mulmod_p(H, Z1), Z2)
    return (X3, Y3, Z3)


def scalar_mul(d: AluDriver, k: int, Pt=G):
    if len(Pt) == 2:
        Pt = (Pt[0], Pt[1], 1)
    k %= N
    R = INF
    Q = Pt
    while k:
        if k & 1:
            R = point_add(d, R, Q)
        Q = point_double(d, Q)
        k >>= 1
    return R


def lift_x(d: AluDriver, x: int, odd: int):
    assert x < P
    y2 = d.addmod_p(d.mulmod_p(d.mulmod_p(x, x), x), 7)
    y = sqrt_p(d, y2)
    assert d.mulmod_p(y, y) == y2, "x 不在曲线上"
    if (y & 1) != odd:
        y = P - y
    return (x, y)


def point_to_affine(d: AluDriver, Pt):
    X, Y, Z = Pt
    if Z == 0:
        return None
    zinv = invmod_p(d, Z)
    zinv2 = d.mulmod_p(zinv, zinv)
    zinv3 = d.mulmod_p(zinv2, zinv)
    return (d.mulmod_p(X, zinv2), d.mulmod_p(Y, zinv3))


def ecrecover(d: AluDriver, z: int, r: int, s: int, recid: int):
    """完整 ecrecover，返回 (Qx, Qy, address) 或 None"""
    if not (1 <= r < N and 1 <= s < N):
        return None
    x = r + (recid >> 1) * N
    if x >= P:
        return None
    try:
        Rx, Ry = lift_x(d, x, recid & 1)
    except AssertionError:
        return None
    R_pt = (Rx, Ry, 1)
    rinv = invmod_n(d, r)
    sR = scalar_mul(d, s % N, R_pt)
    zG = scalar_mul(d, z % N, G)
    if zG[2] != 0:
        neg_zG = (zG[0], d.submod_p(0, zG[1]), zG[2])
    else:
        neg_zG = zG
    diff = point_add(d, sR, neg_zG)
    Q_pt = scalar_mul(d, rinv, diff)
    Q_aff = point_to_affine(d, Q_pt)
    if Q_aff is None:
        return None
    Qx, Qy = Q_aff
    pub = Qx.to_bytes(32, "big") + Qy.to_bytes(32, "big")
    # TODO(上链版): 替换为 cid4 流式 Keccak 组件（SimWithREF 解析链上网表）
    address = keccak256(pub)[12:]
    return (Qx, Qy, address)
