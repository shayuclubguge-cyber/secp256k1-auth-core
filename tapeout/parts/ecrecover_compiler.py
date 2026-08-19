"""
ecrecover_compiler —— ECRECOVER → ecrecover_ctrl 宏指令流编译器（公开、确定性）

信任模型：网表指纹上链 + 本编译器公开 + 逐拍可复放见证。
所有数据相关分支（标量位、奇偶翻转、退化点）在编译期展开 —— 编译器同时是
影子执行器：每发射一条宏指令就按 ctrl 语义精确更新影子寄存器堆，因此编译
完成即知道每个环的最终内容与全部中间值（用于 L2 对拍与链上驱动复放）。

寄存器映射（ctrl 操作数编码 bit3=环选择）：
  ringbusA: X=0  Y=1  Z=2  T1=3  R0=4     —— 累加器 Jacobian + 2 临时
  ringbusB: BX=8 BY=9 T2=10 T3=11 T4=12   —— 仿射基点 + 3 临时
  PIN=14（引脚直供常数） NULL=15（不写回）

宏指令格式：("E", modulus, op, sa, sb, dst, pin_val)
顶层写环：  ("W", dst, value)
"""

from typing import List, Optional, Tuple

from ..field import P, N
from ..engine import GX, GY
from .ecrecover_ctrl import (
    OP_NOP, OP_MOV, OP_EQ0, OP_NE0, OP_LTCHK, OP_ADD, OP_SUB, OP_MUL,
    PIN, NULL,
)

X, Y, Z, T1, R0 = 0, 1, 2, 3, 4
BX, BY, T2, T3, T4 = 8, 9, 10, 11, 12

EXP_P_MINUS_2 = P - 2
EXP_SQRT = (P + 1) // 4

# 基点规格：("ring",) = 读 BX/BY 环；("pin", x, y) = 引脚直供常数点
BASE_RING = ("ring",)


class Compiler:
    def __init__(self):
        self.ops: List[tuple] = []
        self.shadow = [0] * 16
        self.fail = False

    # ---------------- 发射原语（含影子执行） ----------------
    def w(self, dst: int, value: int):
        self.ops.append(("W", dst, value & ((1 << 256) - 1)))
        self.shadow[dst] = value & ((1 << 256) - 1)

    def e(self, m: int, op: int, sa: int, sb: int = NULL, dst: int = NULL,
          pin: Optional[int] = None):
        a = pin if sa == PIN else self.shadow[sa]
        b = pin if sb == PIN else self.shadow[sb]
        if op == OP_MOV:
            r = a
        elif op == OP_EQ0:
            self.fail |= (a != 0)
            r = None
        elif op == OP_NE0:
            self.fail |= (a == 0)
            r = None
        elif op == OP_LTCHK:
            self.fail |= (a >= b)
            r = None
        elif op == OP_ADD:
            r = (a + b) % m
        elif op == OP_SUB:
            r = (a - b) % m
        elif op == OP_MUL:
            assert a < m and b < m, "MUL 操作数必须 < m"
            r = (a * b) % m
        else:
            raise ValueError(op)
        self.ops.append(("E", m, op, sa, sb, dst, pin))
        if dst != NULL and r is not None:
            self.shadow[dst] = r

    # ---------------- 域运算快捷 ----------------
    def _add(self, m, sa, sb, dst, pin=None):
        self.e(m, OP_ADD, sa, sb, dst, pin)

    def _sub(self, m, sa, sb, dst, pin=None):
        self.e(m, OP_SUB, sa, sb, dst, pin)

    def _mul(self, m, sa, sb, dst, pin=None):
        self.e(m, OP_MUL, sa, sb, dst, pin)

    def _mov(self, sa, dst):
        self.e(P, OP_MOV, sa, NULL, dst)

    # ---------------- 点运算（acc = X/Y/Z Jacobian，原地） ----------------
    def _set_inf(self):
        """acc 归一化为 INF = (0,1,0)"""
        self.w(X, 0)
        self.w(Y, 1)
        self.w(Z, 0)

    def point_double(self):
        """7 MUL + 11 ADD/SUB + 2 MOV；公式与 ecrecover_driver.point_double 相同。
        INF/切线垂直（编译期影子可知）→ 归一化为 INF。"""
        if self.shadow[Z] == 0:
            self._set_inf()
            return
        if self.shadow[Y] == 0:
            self._set_inf()
            return
        self._mul(P, X, X, T1)          # XX
        self._mul(P, Y, Y, T2)          # YY
        self._mul(P, T2, T2, T3)        # YYYY
        self._add(P, X, T2, T4)         # X+YY
        self._mul(P, T4, T4, T4)        # (X+YY)²
        self._sub(P, T4, T1, T4)        # −XX
        self._sub(P, T4, T3, T4)        # −YYYY → t
        self._add(P, T4, T4, R0)        # S = 2t
        self._add(P, T1, T1, T4)        # 2XX
        self._add(P, T4, T1, T4)        # M = 3XX
        self._mul(P, T4, T4, T1)        # M²
        self._add(P, R0, R0, T2)        # 2S
        self._sub(P, T1, T2, T1)        # T = M²−2S
        self._sub(P, R0, T1, T2)        # S−T
        self._mul(P, T4, T2, T2)        # M(S−T)
        self._add(P, T3, T3, T4)        # 2Y⁴
        self._add(P, T4, T4, T4)        # 4Y⁴
        self._add(P, T4, T4, T4)        # 8Y⁴
        self._sub(P, T2, T4, T2)        # Y3 = M(S−T) − 8Y⁴
        self._mul(P, Y, Z, T3)          # YZ
        self._add(P, T3, T3, Z)         # Z3 = 2YZ → Z
        self._mov(T1, X)                # X = T
        self._mov(T2, Y)                # Y = Y3

    def point_add(self, base=BASE_RING):
        """acc += 仿射基点（Z2=1），11 MUL；base 环驻或引脚直供。
        退化（编译期检测）：acc=INF → acc=基点；H≡0 且 Rr≡0 → 倍点；
        H≡0 且 Rr≢0 → acc=INF。"""
        if base == BASE_RING:
            bx, by = BX, BY
            pin_x = pin_y = None
        else:
            _, px, py = base
            bx = by = PIN
            pin_x, pin_y = px, py
        # acc = INF → 结果为基点（仿射载入）
        if self.shadow[Z] == 0:
            self._init_acc(base)
            return
        # 退化检测（影子值，Jacobian→仿射比较 x：X2·Z1² vs X1）
        z1 = self.shadow[Z]
        h_sh = (self.shadow[bx] if bx != PIN else pin_x) * z1 % P * z1 % P
        h_sh = (h_sh - self.shadow[X]) % P
        if h_sh == 0:
            rr_sh = (self.shadow[by] if by != PIN else pin_y) \
                * z1 % P * z1 % P * z1 % P
            rr_sh = (rr_sh - self.shadow[Y]) % P
            if rr_sh == 0:
                self.point_double()
            else:
                self._set_inf()
            return
        self._mul(P, Z, Z, T1)                    # Z1Z1
        self._mul(P, bx, T1, T2, pin=pin_x)       # U2 = bx·Z1Z1
        self._mul(P, T1, Z, T1)                   # Z1³
        self._mul(P, by, T1, T1, pin=pin_y)       # S2 = by·Z1³
        self._sub(P, T2, X, T2)                   # H = U2−X1
        self._sub(P, T1, Y, T1)                   # Rr = S2−Y1
        self._mul(P, T2, T2, T3)                  # H2
        self._mul(P, T3, T2, T4)                  # H3
        self._mul(P, T2, Z, Z)                    # Z3 = H·Z1 → Z
        self._mul(P, X, T3, R0)                   # U1H2（X 末次使用）
        self._mul(P, Y, T4, X)                    # YH3 = Y1·H3 → X 环复用
        self._mul(P, T1, T1, T3)                  # Rr²
        self._sub(P, T3, T4, T3)                  # −H3
        self._add(P, R0, R0, T4)                  # 2U1H2
        self._sub(P, T3, T4, T3)                  # X3 ✓
        self._sub(P, R0, T3, T4)                  # U1H2−X3
        self._mul(P, T1, T4, T1)                  # Rr·(U1H2−X3)
        self._sub(P, T1, X, Y)                    # Y3 → Y
        self._mov(T3, X)                          # X = X3

    # ---------------- 标量乘（编译期分支展开） ----------------
    def _init_acc(self, base):
        """acc = 基点仿射（Z=1）"""
        if base == BASE_RING:
            self._mov(BX, X)
            self._mov(BY, Y)
        else:
            _, px, py = base
            self.w(X, px)
            self.w(Y, py)
        self.w(Z, 1)

    def scalar_mul_shamir(self, k1: int, base1, k2: int, base2):
        """acc = k1·B1 + k2·B2（Shamir 共累加器，MSB-first，分支编译期展开）"""
        assert 0 <= k1 < N and 0 <= k2 < N
        started = False
        for i in range(255, -1, -1):
            b1 = (k1 >> i) & 1
            b2 = (k2 >> i) & 1
            if not started:
                if b1:
                    self._init_acc(base1)
                    started = True
                    if b2:
                        self.point_add(base2)
                elif b2:
                    self._init_acc(base2)
                    started = True
                continue
            self.point_double()
            if b1:
                self.point_add(base1)
            if b2:
                self.point_add(base2)
        return started

    def scalar_mul_single(self, k: int, base):
        """acc = k·B（基点在 BX/BY 环或引脚）"""
        started = False
        for i in range(255, -1, -1):
            if not (k >> i) & 1 and not started:
                continue
            if not started:
                self._init_acc(base)
                started = True
                continue
            self.point_double()
            if (k >> i) & 1:
                self.point_add(base)
        return started

    # ---------------- 幂（指数编译期展开） ----------------
    def powmod(self, dst: int, base_reg: int, exp: int, m: int):
        """dst = base^exp mod m；base_reg 全程保持。MSB-first 平方-乘。"""
        assert exp > 0
        bits = bin(exp)[3:]               # 去掉 '0b1'（首位的 1）
        self._mov(base_reg, dst)          # acc = base（吸收最高位）
        for bit in bits:
            self._mul(m, dst, dst, dst)
            if bit == "1":
                self._mul(m, dst, base_reg, dst)

    def affine_to_base(self):
        """acc(Jacobian) → BX/BY 仿射（zinv = Z^(p−2) 链上算）"""
        self.e(P, OP_NE0, Z)              # 断言 Z≠0（INF → fail）
        self.powmod(T1, Z, EXP_P_MINUS_2, P)   # T1 = zinv
        self._mul(P, T1, T1, T2)          # zinv²
        self._mul(P, X, T2, BX)           # x_aff
        self._mul(P, T2, T1, T2)          # zinv³
        self._mul(P, Y, T2, BY)           # y_aff

    def affine_inplace(self):
        """acc(Jacobian) → X/Y 原地仿射化"""
        self.e(P, OP_NE0, Z)
        self.powmod(T1, Z, EXP_P_MINUS_2, P)
        self._mul(P, T1, T1, T2)          # zinv²
        self._mul(P, X, T2, X)            # X ← x_aff
        self._mul(P, T2, T1, T2)          # zinv³
        self._mul(P, Y, T2, Y)            # Y ← y_aff

    # ---------------- lift_x ----------------
    def lift_x(self, recid_odd: int):
        """Bx=x 已在 BX 环 → y 计算进 BY；奇偶按编译期影子展开翻转"""
        self._mul(P, BX, BX, T1)          # x²
        self._mul(P, T1, BX, T1)          # x³
        self._add(P, T1, PIN, T4, pin=7)  # y2 = x³+7
        self.powmod(BY, T4, EXP_SQRT, P)  # y = y2^((p+1)/4)
        self._mul(P, BY, BY, T1)          # 校验 y²==y2
        self._sub(P, T1, T4, T1)
        self.e(P, OP_EQ0, T1)             # 不等 → fail（x 不在曲线上）
        if (self.shadow[BY] & 1) != recid_odd:
            self._sub(P, PIN, BY, BY, pin=P)   # y = p − y

    # ---------------- 完整 ECRECOVER ----------------
    def compile_ecrecover(self, z: int, r: int, s: int, recid: int,
                          rinv: int):
        """
        编译完整 ECRECOVER。rinv 由驱动器链下供给（与 s/z 同信任模型），
        链上用 1 次 MUL mod n 闭合一致性：rinv·r ≡ 1 (mod n)，否则 fail。
        结束后 Qx/Qy 在 X/Y 环（仿射），驱动器 READ_RING 读出。
        """
        assert (r * rinv) % N == 1, "驱动器供给的 rinv 与 r 不一致"
        # --- 输入校验（mod n 域） ---
        self.w(T1, r)
        self.e(N, OP_NE0, T1)                     # r ≠ 0
        self.e(N, OP_LTCHK, T1, PIN, pin=N)       # r < n
        self.w(T2, s)
        self.e(N, OP_NE0, T2)                     # s ≠ 0
        self.e(N, OP_LTCHK, T2, PIN, pin=N)       # s < n
        # x = r + (recid>>1)·n
        if recid >> 1:
            self._add(N, T1, PIN, BX, pin=N)
        else:
            self._mov(T1, BX)
        self.e(P, OP_LTCHK, BX, PIN, pin=P)       # x < p
        # rinv 一致性：rinv·r ≡ 1 (mod n)
        self.w(T4, rinv)
        self._mul(N, T4, T1, T1)
        self._sub(N, T1, PIN, T1, pin=1)
        self.e(N, OP_EQ0, T1)
        # --- lift_x → R 仿射在 (BX, BY) ---
        self.lift_x(recid & 1)
        # --- Shamir：acc = s·R + z·(−G) = sR − zG（基点 −G 常数走引脚） ---
        mG = ("pin", GX, (P - GY) % P)
        started = self.scalar_mul_shamir(s, BASE_RING, z % N, mG)
        assert started, "s 与 −z 全零（非法签名）"
        self.e(P, OP_NE0, Z)                      # diff ≠ INF
        # --- diff 仿射化 → BX/BY ---
        self.affine_to_base()
        # --- Q = rinv·diff ---
        self.scalar_mul_single(rinv, BASE_RING)
        # --- Q 原地仿射化 → X/Y ---
        self.affine_inplace()
        return self

    # ---------------- 结果 ----------------
    @property
    def qx(self) -> int:
        return self.shadow[X]

    @property
    def qy(self) -> int:
        return self.shadow[Y]

    def stats(self) -> str:
        n_mul = sum(1 for o in self.ops if o[0] == "E" and o[2] == OP_MUL)
        n_w = sum(1 for o in self.ops if o[0] == "W")
        return (f"{len(self.ops):,} 条宏指令（MUL={n_mul:,}, WRITE={n_w}, "
                f"其他={len(self.ops) - n_mul - n_w:,}）")


def run_program(driver, ops: List[tuple], check_fail=True):
    """把编译产物灌进 CtrlDriver（拍级，可链上复放）"""
    for o in ops:
        if o[0] == "W":
            _, dst, val = o
            driver.write_ring(dst, val)
        else:
            _, m, op, sa, sb, dst, pin = o
            driver.m = m
            driver.rc = (1 << 256) - m
            driver.exec(op, sa, sb, dst,
                        pin_a=pin if sa == PIN else None,
                        pin_b=pin if sb == PIN else None)
    if check_fail:
        assert driver.fail_expected == compiler_fail(ops), \
            "影子 fail 与编译器不一致"


def compiler_fail(ops) -> bool:
    """独立影子重放 fail 语义（轻量校验）"""
    sh = [0] * 16
    fail = False
    for o in ops:
        if o[0] == "W":
            sh[o[1]] = o[2]
            continue
        _, m, op, sa, sb, dst, pin = o
        a = pin if sa == PIN else sh[sa]
        b = pin if sb == PIN else sh[sb]
        if op == OP_EQ0:
            fail |= (a != 0)
        elif op == OP_NE0:
            fail |= (a == 0)
        elif op == OP_LTCHK:
            fail |= (a >= b)
        elif op == OP_MOV:
            sh[dst] = a
        elif op == OP_ADD:
            sh[dst] = (a + b) % m
        elif op == OP_SUB:
            sh[dst] = (a - b) % m
        elif op == OP_MUL:
            sh[dst] = (a * b) % m
    return fail
