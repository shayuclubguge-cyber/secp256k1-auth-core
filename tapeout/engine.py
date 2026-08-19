"""
ECRECOVER 周期级架构模型（Register-Transfer Model）

与门级网表使用完全相同的算法与数据通路结构：
  - hw_mulmod : 与 tapeout/modmul.py 相同的 256 拍移位-加迭代
  - hw_addmod / hw_submod : 与 tapeout/field.py 相同的 mod-p 加减
  - hw_invmod : 费马小定理 a^(p-2) mod p，平方-乘调度（硬件上复用乘法数据通路）
  - 点运算    : Jacobian 坐标（避免逐次求逆），倍点 8 次乘、加点 16 次乘
  - ecrecover : Q = r^-1 (sR - zG)，地址 = keccak256(Qx||Qy)[12:]

用途：
  1. 端到端功能验证（真实以太坊向量）；
  2. 作为分层仿真中的函数式 REF 模型；
  3. 为硬件控制 FSM 提供逐拍的黄金参考。
"""

from typing import Optional, Tuple

from .field import P, N

# secp256k1 生成元
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G = (GX, GY)

MASK256 = (1 << 256) - 1


# ---------------------------------------------------------------------------
# 与门级数据通路一致的域运算模型
# ---------------------------------------------------------------------------

def hw_addmod(a: int, b: int) -> int:
    """mod-p 加法（对应 field.add_mod：快速约减 + 条件减 p）"""
    return (a + b) % P


def hw_submod(a: int, b: int) -> int:
    """mod-p 减法（对应 field.sub_mod）"""
    return (a - b) % P


def hw_mulmod(a: int, b: int) -> int:
    """
    mod-p 乘法 —— 与 modmul.py 的时序数据通路逐拍一致：
      R=0; 循环256次: if B&1: R=(R+A)%p; A=2A%p; B>>=1
    """
    A, B, R = a, b, 0
    for _ in range(256):
        if B & 1:
            R = (R + A) % P
        A = (A + A) % P
        B >>= 1
    return R


def hw_invmod(a: int) -> int:
    """
    mod-p 逆元 —— 费马小定理 a^(p-2) mod p，平方-乘调度。
    硬件上复用乘法数据通路：256 次平方 + 至多 256 次乘（p-2 含 254 个 1）。
    """
    assert a % P != 0, "0 没有逆元"
    e = P - 2
    base = a % P
    acc = 1
    for i in range(256):
        if (e >> i) & 1:
            acc = hw_mulmod(acc, base)
        base = hw_mulmod(base, base)
    return acc


# ---------------------------------------------------------------------------
# Jacobian 坐标点运算（硬件友好：倍点/加点无需求逆）
#   点 = (X, Y, Z) 表示仿射 (X/Z^2, Y/Z^3)；无穷远 Z=0
# ---------------------------------------------------------------------------

INF = (0, 1, 0)


def point_double(Pt: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """Jacobian 倍点：8 次 hw_mulmod"""
    X, Y, Z = Pt
    if Z == 0 or Y == 0:
        return INF
    XX = hw_mulmod(X, X)
    YY = hw_mulmod(Y, Y)
    YYYY = hw_mulmod(YY, YY)
    ZZ = hw_mulmod(Z, Z)
    # S = 2*((X+YY)^2 - XX - YYYY)
    t = hw_addmod(X, YY)
    t = hw_mulmod(t, t)
    t = hw_submod(t, XX)
    t = hw_submod(t, YYYY)
    S = hw_mulmod(2, t)
    M = hw_mulmod(3, XX)  # a=0: M = 3*XX
    T = hw_submod(hw_mulmod(M, M), hw_mulmod(2, S))     # T = M^2 - 2S
    Y3 = hw_submod(hw_mulmod(M, hw_submod(S, T)), hw_mulmod(8, YYYY))
    Z3 = hw_mulmod(2, hw_mulmod(Y, Z))
    return (T, Y3, Z3)


def point_add(P1: Tuple[int, int, int], P2: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """Jacobian 加点（混合坐标 P2 仿射亦可，这里全 Jacobian）：约 16 次 hw_mulmod"""
    X1, Y1, Z1 = P1
    X2, Y2, Z2 = P2
    if Z1 == 0:
        return P2
    if Z2 == 0:
        return P1
    Z1Z1 = hw_mulmod(Z1, Z1)
    Z2Z2 = hw_mulmod(Z2, Z2)
    U1 = hw_mulmod(X1, Z2Z2)
    U2 = hw_mulmod(X2, Z1Z1)
    S1 = hw_mulmod(hw_mulmod(Y1, Z2), Z2Z2)
    S2 = hw_mulmod(hw_mulmod(Y2, Z1), Z1Z1)
    if U1 == U2:
        if S1 != S2:
            return INF
        return point_double(P1)
    H = hw_submod(U2, U1)
    HH = hw_mulmod(H, H)
    I = hw_mulmod(4, HH)
    J = hw_mulmod(H, I)
    r = hw_mulmod(2, hw_submod(S2, S1))
    V = hw_mulmod(U1, I)
    X3 = hw_submod(hw_submod(hw_mulmod(r, r), J), hw_mulmod(2, V))
    Y3 = hw_submod(hw_mulmod(r, hw_submod(V, X3)), hw_mulmod(2, hw_mulmod(S1, J)))
    # Z3 = ((Z1+Z2)^2 - Z1Z1 - Z2Z2) * H
    t = hw_addmod(Z1, Z2)
    t = hw_mulmod(t, t)
    t = hw_submod(t, Z1Z1)
    t = hw_submod(t, Z2Z2)
    Z3 = hw_mulmod(t, H)
    return (X3, Y3, Z3)


def point_to_affine(Pt: Tuple[int, int, int]) -> Optional[Tuple[int, int]]:
    X, Y, Z = Pt
    if Z == 0:
        return None
    zinv = hw_invmod(Z)
    zinv2 = hw_mulmod(zinv, zinv)
    zinv3 = hw_mulmod(zinv2, zinv)
    return (hw_mulmod(X, zinv2), hw_mulmod(Y, zinv3))


def scalar_mul(k: int, Pt=G) -> Tuple[int, int, int]:
    """double-and-add 标量乘（标量域 mod n），Pt 可为仿射或 Jacobian"""
    if len(Pt) == 2:
        Pt = (Pt[0], Pt[1], 1)
    k %= N
    R = INF
    Q = Pt
    while k:
        if k & 1:
            R = point_add(R, Q)
        Q = point_double(Q)
        k >>= 1
    return R


def lift_x(x: int, odd: int) -> Tuple[int, int]:
    """由 x 恢复曲线点（p % 4 == 3，开方 = pow(y2, (p+1)/4)）"""
    assert x < P, "x 必须 < p"
    y2 = hw_addmod(hw_mulmod(hw_mulmod(x, x), x), 7)
    # sqrt 用平方-乘调度（指数 (p+1)/4）
    e = (P + 1) // 4
    base, acc = y2, 1
    for i in range(256):
        if (e >> i) & 1:
            acc = hw_mulmod(acc, base)
        base = hw_mulmod(base, base)
    y = acc
    assert hw_mulmod(y, y) == y2, "x 不在曲线上"
    if (y & 1) != odd:
        y = P - y
    return (x, y)


# ---------------------------------------------------------------------------
# ECRECOVER 顶层
# ---------------------------------------------------------------------------

def ecrecover(z: int, r: int, s: int, recid: int) -> Optional[Tuple[int, int, bytes]]:
    """
    以太坊 ecrecover：
      输入: z(消息哈希), r, s, recid(0..3, bit0=y奇偶, bit1=x是否>=n)
      输出: (Qx, Qy, address[20]) 或 None（签名非法）
    """
    if not (1 <= r < N and 1 <= s < N):
        return None
    x = r + (recid >> 1) * N
    if x >= P:
        return None
    try:
        Rx, Ry = lift_x(x, recid & 1)
    except AssertionError:
        return None

    R_pt = (Rx, Ry, 1)
    # 可选验证 nR = INF（硬件上跳过，链上预编译也不做）
    rinv = pow(r, -1, N)  # 标量域逆元（mod n）
    sR = scalar_mul(s % N, R_pt)
    zG = scalar_mul(z % N, G)
    # Q = rinv * (sR - zG)
    if zG[2] != 0:
        neg_zG = (zG[0], hw_submod(0, zG[1]), zG[2])
    else:
        neg_zG = zG
    diff = point_add(sR, neg_zG)
    Q_pt = scalar_mul(rinv, diff)
    Q_aff = point_to_affine(Q_pt)
    if Q_aff is None:
        return None
    Qx, Qy = Q_aff
    pub = Qx.to_bytes(32, "big") + Qy.to_bytes(32, "big")
    address = keccak256(pub)[12:]
    return (Qx, Qy, address)


def make_signature(z: int, d: int, k: int) -> Tuple[int, int, int]:
    """用私钥 d、临时随机数 k 对 z 构造 ECDSA 签名（测试向量生成用）"""
    R_aff = point_to_affine(scalar_mul(k, G))
    Rx, Ry = R_aff
    r = Rx % N
    recid = (Ry & 1) | ((1 if Rx >= N else 0) << 1)
    s = (pow(k, -1, N) * (z + r * d)) % N
    return (r, s, recid)


# ---------------------------------------------------------------------------
# 纯 Python Keccak-256（与链上 EVM keccak256 一致；非 SHA3-256）
# ---------------------------------------------------------------------------

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

# 旋转偏移 r[x][y]（x 列，y 行）
_KECCAK_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]

_MASK64 = (1 << 64) - 1


def _rotl64(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK64 if n else x


def _keccak_f(st: list):
    """Keccak-f[1600] 置换，st 为 25 个 64 位 lane（索引 x + 5y）"""
    for rc in _KECCAK_RC:
        # θ
        C = [st[x] ^ st[x + 5] ^ st[x + 10] ^ st[x + 15] ^ st[x + 20]
             for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rotl64(C[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] ^= D[x]
        # ρ + π
        B = [0] * 25
        for x in range(5):
            for y in range(5):
                B[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    st[x + 5 * y], _KECCAK_ROT[x][y])
        # χ
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] = B[x + 5 * y] ^ (
                    (~B[(x + 1) % 5 + 5 * y] & _MASK64) & B[(x + 2) % 5 + 5 * y])
        # ι
        st[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256（EVM 兼容，填充 0x01..0x80，rate=136 字节）"""
    rate = 136
    st = [0] * 25
    # 填充
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)
    # 吸收
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i:8 * i + 8], "little")
            st[i] ^= lane
        _keccak_f(st)
    # 挤出 32 字节
    out = bytearray()
    for i in range(4):
        out += st[i].to_bytes(8, "little")
    return bytes(out[:32])
