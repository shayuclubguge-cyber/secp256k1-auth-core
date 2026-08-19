"""
时序 U256 模 p 乘法器（NAND + LATCH，移位-加算法）

接口协议（513 入 → 256 出）：
  输入: start(1), a[256], b[256]   —— 均为小端位序
  输出: result[256]                —— (a * b) mod p

拍序：
  拍 0   : start=1，a/b 有效 → 载入 A/B 寄存器，R 清零
  拍 1..256: start=0          → 每拍执行一次迭代：
               if B[0]: R = (R + A) mod p
               A = 2A mod p ; B >>= 1
  拍 256 后: result 稳定有效（B 已排空，继续打拍 result 不变）

数据通路每拍约 3.6 万门（两个 mod-p 加法 + 三总线 MUX + 768 LATCH）。
"""

from typing import List, Tuple

from . import Circuit, Signal
from .field import P, add_mod, mux_bus


def build_mulmod_step(c: Circuit, A: List[Signal], B: List[Signal],
                      R: List[Signal], start: Signal,
                      a_in: List[Signal], b_in: List[Signal]):
    """
    构建单拍迭代组合逻辑（寄存器由调用方持有）。
    返回 (next_A, next_B, next_R)。
    """
    cond = B[0]
    sum_ra = add_mod(c, R, A)          # R + A (mod p)
    dbl_a = add_mod(c, A, A)           # 2A (mod p)

    # 迭代值
    iter_R = mux_bus(c, cond, R, sum_ra)
    iter_B = B[1:] + [c.zero()]        # B >>= 1

    # start=1 时载入新操作数（优先于迭代）
    next_R = mux_bus(c, start, iter_R, [c.zero()] * 256)
    next_A = mux_bus(c, start, dbl_a, a_in)
    next_B = mux_bus(c, start, iter_B, b_in)
    return next_A, next_B, next_R


def build_mulmod_seq() -> Tuple[Circuit, List[Signal]]:
    """
    时序模 p 乘法器。
    返回 (circuit, result_signals[256])。
    """
    c = Circuit("u256_mulmod_seq", n_in=513, n_out=256)
    pins = c.input_signals()
    start = pins[0]
    a_in = pins[1:257]
    b_in = pins[257:513]

    # 先占位寄存器（输出信号立即可用，读的是上一拍旧值）
    A = c.latch_bus(256)
    B = c.latch_bus(256)
    R = c.latch_bus(256)

    # 组合逻辑：单拍迭代
    next_A, next_B, next_R = build_mulmod_step(c, A, B, R, start, a_in, b_in)

    # 闭环：寄存器输入 = 下一拍值
    c.drive_bus(A, next_A)
    c.drive_bus(B, next_B)
    c.drive_bus(R, next_R)

    # 输出封存：result = R（两阶段 NOT，保证输出恰好是最后 256 个信号）
    out = c.seal_outputs(R)
    return c, out
