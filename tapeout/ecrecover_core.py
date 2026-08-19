"""
ECRECOVER 旗舰核 —— 顶层组装（域运算 ALU 版）

一块 72 入 / 65 出的有状态电路（引脚 ≤255，可被更大的顶层 REF）。
内部集成：
  - 3 个 256 位域寄存器 A / B / R
  - mod-p 加法器 ×2、减法器、乘法迭代数据通路（复用加法器结构）
  - 8 位迭代计数器 + busy 标志（MUL 固定 256 拍）

指令协议（每拍一条）：
  LOAD: load_en=1, load_sel=0/1 (A/B), data_in=64位字
        —— LSB 字优先，4 拍装满一个寄存器
  MUL : start=1, op=01          —— R <= 0, busy<=1；随后 256 拍迭代
        （A 自加倍、B 右移、按 B[0] 条件累加进 R），busy 归零后完成
  ADD : start=1, op=10          —— 单拍 R <= (A+B) mod p
  SUB : start=1, op=11          —— 单拍 R <= (A-B) mod p
  READ: rd_sel=0..3             —— data_out 输出 R 的 64 位字（组合直读）

完整 ECRECOVER（求逆/点运算/标量乘）由拍序驱动器在此核上调度；
黄金参考见 tapeout/engine.py（周期级模型，算法与本数据通路一致）。
"""

from typing import List, Tuple

from . import Circuit, Signal
from .field import P, add_mod, sub_mod, mux_bus, eq_const

OP_MUL = 1
OP_ADD = 2
OP_SUB = 3


def build_ecrecover_core() -> Tuple[Circuit, List[Signal], Signal]:
    """
    返回 (circuit, data_out[64], busy)
    引脚: op[2] start[1] load_sel[2] load_en[1] data_in[64] rd_sel[2] = 72
    输出: data_out[64] busy[1] = 65
    """
    c = Circuit("ecrecover_core", n_in=72, n_out=65)
    pins = c.input_signals()
    op = pins[0:2]
    start = pins[2]
    load_sel = pins[3:5]
    load_en = pins[5]
    data_in = pins[6:70]
    rd_sel = pins[70:72]

    # ---- 寄存器（先占位，输出读旧值） ----
    A = c.latch_bus(256)
    B = c.latch_bus(256)
    R = c.latch_bus(256)
    busy = c.latch_reg()
    cnt = c.latch_bus(8)

    # ---- 控制译码 ----
    is_mul = c.and_(c.not_(op[1]), op[0])       # op == 01
    is_add = c.and_(op[1], c.not_(op[0]))       # op == 10
    is_sub = c.and_(op[1], op[0])               # op == 11
    sel_a = c.and_(c.not_(load_sel[1]), c.not_(load_sel[0]))
    sel_b = c.and_(c.not_(load_sel[1]), load_sel[0])
    load_a = c.and_(load_en, sel_a)
    load_b = c.and_(load_en, sel_b)
    do_mul = c.and_(start, is_mul)
    do_add = c.and_(start, is_add)
    do_sub = c.and_(start, is_sub)

    # ---- 数据通路（组合） ----
    add_ab = add_mod(c, A, B)                   # A+B (mod p)
    sub_ab = sub_mod(c, A, B)                   # A-B (mod p)
    sum_ra = add_mod(c, R, A)                   # R+A (mod p) —— 乘法条件累加
    dbl_a = add_mod(c, A, A)                    # 2A  (mod p) —— 乘法自加倍

    # ---- 载入路径：reg <= (reg >> 64) | (data << 192)，LSB字优先 ----
    shifted_A = A[64:] + list(data_in)
    shifted_B = B[64:] + list(data_in)

    # ---- 乘法迭代 ----
    cond = B[0]
    iter_R = mux_bus(c, cond, R, sum_ra)
    iter_B = B[1:] + [c.zero()]
    iter_done = eq_const(c, cnt, 255)

    # ---- 下一拍状态 ----
    # R: busy 时迭代累加；do_sub/do_add 单拍写入；do_mul 清零；否则保持
    r_v = mux_bus(c, busy, R, iter_R)           # busy 时迭代
    r_v = mux_bus(c, do_sub, r_v, sub_ab)       # SUB
    r_v = mux_bus(c, do_add, r_v, add_ab)       # ADD
    r_v = mux_bus(c, do_mul, r_v, [c.zero()] * 256)  # MUL 启动清零
    next_R = r_v

    # A: load 优先；其次 busy 迭代加倍；否则保持
    next_A = mux_bus(c, busy, A, dbl_a)
    next_A = mux_bus(c, load_a, next_A, shifted_A)

    # B: load 优先；其次 busy 迭代右移；否则保持
    next_B = mux_bus(c, busy, B, iter_B)
    next_B = mux_bus(c, load_b, next_B, shifted_B)

    # busy: do_mul 置1；busy 且计满 255 清零；否则保持
    next_busy = c.mux(iter_done, busy, c.zero())
    next_busy = c.mux(do_mul, next_busy, c.one())

    # cnt: do_mul 清零；busy 时 +1；否则保持
    inc = []
    carry = c.one()
    for i in range(8):
        inc.append(c.xor(cnt[i], carry))
        carry = c.and_(cnt[i], carry)
    next_cnt = mux_bus(c, busy, cnt, inc)
    next_cnt = mux_bus(c, do_mul, next_cnt, [c.zero()] * 8)

    # ---- 闭环 ----
    c.drive_bus(A, next_A)
    c.drive_bus(B, next_B)
    c.drive_bus(R, next_R)
    c.drive_latch(busy, next_busy)
    c.drive_bus(cnt, next_cnt)

    # ---- 输出：R 的 64 位字选择 + busy ----
    word = R[0:64]
    word = mux_bus(c, rd_sel[0], word, R[64:128])       # bit0: 0/1
    hi = mux_bus(c, rd_sel[0], R[128:192], R[192:256])  # bit0: 2/3
    data_out = mux_bus(c, rd_sel[1], word, hi)
    out = c.seal_outputs(list(data_out) + [busy])
    return c, out[:-1], out[-1]
