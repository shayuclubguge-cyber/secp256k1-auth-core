"""
U256 素数域运算构建器 —— secp256k1
  p = 2^256 - 2^32 - 977           （基域，点坐标）
  n = 0xFFFF...FEBAAE...364141     （标量域，签名 r/s）

所有总线均为小端位序：bits[0] 是最低位。
add_mod / sub_mod 支持任意 256 位模数（p 或 n）；
mod p 的加法带快速约减（2^256 ≡ 2^32 + 977 (mod p)）。
"""

from typing import List, Optional, Tuple

from . import Circuit, Signal

# secp256k1 基域素数 p
P = (1 << 256) - (1 << 32) - 977

# secp256k1 曲线阶 n（标量域）
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

# 快速约减常数：2^256 ≡ R (mod p)
R_MOD = (1 << 32) + 977


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def const_bits(c: Circuit, value: int, width: int = 256) -> List[Signal]:
    """常量总线"""
    return [c.one() if (value >> i) & 1 else c.zero() for i in range(width)]


def int_to_bits(value: int, width: int = 256) -> List[bool]:
    """Python int -> 小端 bool 总线（仿真输入用）"""
    return [bool((value >> i) & 1) for i in range(width)]


def bits_to_int(bits) -> int:
    """小端 bool 总线 -> Python int（仿真输出用）"""
    return sum(int(b) << i for i, b in enumerate(bits))


def add_256(c: Circuit, a: List[Signal], b: List[Signal],
            cin: Optional[Signal] = None) -> Tuple[List[Signal], Signal]:
    """256 位行波加法：a + b + cin -> (sum[256], cout)"""
    carry = cin if cin is not None else c.zero()
    out = []
    for i in range(256):
        s, carry = c.adder(a[i], b[i], carry)
        out.append(s)
    return out, carry


def sub_256(c: Circuit, a: List[Signal], b: List[Signal]) -> Tuple[List[Signal], Signal]:
    """256 位减法：a - b -> (diff[256], bout)。bout=1 表示 a < b"""
    # a - b = a + NOT(b) + 1；bout = NOT(cout)
    nb = [c.not_(x) for x in b]
    diff, cout = add_256(c, a, nb, c.one())
    return diff, c.not_(cout)


def mux_bus(c: Circuit, sel: Signal, a: List[Signal], b: List[Signal]) -> List[Signal]:
    """位总线二选一：sel=0 选 a，sel=1 选 b"""
    ns = c.not_(sel)
    out = []
    for i in range(len(a)):
        t1 = c.and_(a[i], ns)
        t2 = c.and_(b[i], sel)
        out.append(c.or_(t1, t2))
    return out


def eq_const(c: Circuit, bits: List[Signal], value: int) -> Signal:
    """总线 == 常量 的译码器"""
    terms = []
    for i, sig in enumerate(bits):
        if (value >> i) & 1:
            terms.append(sig)
        else:
            terms.append(c.not_(sig))
    t = terms[0]
    for x in terms[1:]:
        t = c.and_(t, x)
    return t


# ---------------------------------------------------------------------------
# 模运算
# ---------------------------------------------------------------------------

def add_mod(c: Circuit, a: List[Signal], b: List[Signal],
            modulus: int = P) -> List[Signal]:
    """
    (a + b) mod modulus，要求 0 <= a, b < modulus。
    快速约减：sum = a + b（257位）。当进位 cout=1 时，
    sum - 2^256 + R ≡ sum (mod m)，其中 R = 2^256 - m
    （mod p 时 R = 2^32 + 977，即"2^256 ≡ 2^32+977 (mod p)"）。
    此时 adjusted = sum_lo + R = sum - m < m，不会溢出 256 位。
    最后统一做一次条件减模（处理 cout=0 且 sum >= m 的情形，
    sum < 2m 保证减一次足够）。
    """
    sum_lo, cout = add_256(c, a, b)
    m_bits = const_bits(c, modulus)

    # cout=1 时 sum_lo += R（R = 2^256 - m，精确成立，非近似）
    R = (1 << 256) - modulus
    r_sel = mux_bus(c, cout, const_bits(c, 0), const_bits(c, R))
    adjusted, _ = add_256(c, sum_lo, r_sel)

    # 候选值减模数；若未借位（candidate >= m）则取差
    diff, bout = sub_256(c, adjusted, m_bits)
    need_reduce = c.not_(bout)
    return mux_bus(c, need_reduce, adjusted, diff)


def sub_mod(c: Circuit, a: List[Signal], b: List[Signal],
            modulus: int = P) -> List[Signal]:
    """(a - b) mod modulus，要求 0 <= a, b < modulus"""
    diff, bout = sub_256(c, a, b)
    # 借位时加回模数
    m_sel = mux_bus(c, bout, const_bits(c, 0), const_bits(c, modulus))
    out, _ = add_256(c, diff, m_sel)
    return out


def dbl_mod(c: Circuit, a: List[Signal], modulus: int = P) -> List[Signal]:
    """2a mod modulus"""
    return add_mod(c, a, a, modulus)
