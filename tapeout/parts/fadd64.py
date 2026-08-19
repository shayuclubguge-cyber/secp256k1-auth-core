"""
fadd64 —— 字串行 256 位加/减 ALU（旗舰核第一个零件，移位寄存器版 v2）

v2 重构动机：v1（随机访问字选择 + 按字写使能，6,028 门）流片需 ~18.2M gas，
被 OKX 钱包静默丢弃（2026-08-18 两次实测）。移位寄存器结构把字选择和
写使能全部变成纯连线，门数降到 ~2.6k，gas ~7.5M，OKX 可发。

结构：ACC 是 256 位移位寄存器，每拍下移 64 位，新字从顶部插入；
4 拍恰好旋转一周，字序自动复原。无字选择 mux、无按字写使能。

引脚（67 入 / 65 出）：

  输入：
    in_word[63:0]  pin 0..63   本拍输入的 64 位字（bit k = pin k，LSW 先行）
    op[1:0]        pin 64..65  0=LOAD 1=ADD 2=SUB 3=READ
    start          pin 66      4 拍组的首拍置 1（复位进位链）

  输出：
    out_word[63:0] pin 0..63   ADD/SUB: 本拍新算出的和字（4 拍即得全结果，
                               无需单独 READ）；LOAD: 回显输入字；READ: ACC 底字
    cout           pin 64      进位寄存器。运算第 4 拍结束后（LATCH 旧值语义，
                               从第 5 拍起）可读：ADD 溢出=1；SUB 借位 = NOT cout

操作语义（每个运算必须恰好 4 拍，字按 LSW 先行）：
  LOAD: ACC <= 流入的 256 位（顶部插入，4 拍装满）
  ADD : 每拍 sum = ACC底字 + in_word + carry；sum 从顶部插回
  SUB : 同 ADD，in_word 按位取反且 start 拍 carry 预置 1（a + ~b + 1）
  READ: 纯旋转，ACC 不变，out_word 流出 ACC 底字
  非算术拍 carry 保持不变；start 拍 carry 强制 = is_sub。

mod-p 加法完整流程（驱动器编排，可用免费 view step 跑）：
  LOAD a → ADD b →（若 cout=1）ADD R [R=2^32+977] → SUB p →（若 bout=1）ADD p
mod-n 同理，换常数即可（R_n = 2^256 - n）。

两遍 LATCH 语义：LATCH 输出旧值，d 在拍末采样（已与链上 beat 行为对拍确认）。
"""

from typing import List

from .. import Circuit, Signal

# 操作码
OP_LOAD, OP_ADD, OP_SUB, OP_READ = 0, 1, 2, 3


def build_fadd64() -> Circuit:
    c = Circuit("fadd64", n_in=67, n_out=65)
    pins = c.input_signals()
    in_word = pins[0:64]
    op0, op1 = pins[64], pins[65]
    start = pins[66]

    # 操作译码
    is_load = c.and_(c.not_(op1), c.not_(op0))   # 00
    is_arith = c.xor(op0, op1)                   # 01 或 10
    is_sub = c.and_(op1, c.not_(op0))            # 10

    # --- 状态：256 位移位寄存器 + 进位 ---
    acc = c.latch_bus(256)      # acc[0]=ACC bit0（底字最低位）
    carry = c.latch_reg()

    bottom = acc[0:64]          # 本拍底字（旧值）

    # --- 64 位加/减法器（输入直接是底字，零 mux） ---
    b_in = [c.xor(in_word[k], is_sub) for k in range(64)]
    carry_eff = c.mux(start, carry, is_sub)      # start 拍预置：SUB→1 否则→0
    sum_word: List[Signal] = []
    cry = carry_eff
    for k in range(64):
        s, cry = c.adder(bottom[k], b_in[k], cry)
        sum_word.append(s)
    carry64 = cry

    # carry 更新：算术拍采样进位，LOAD/READ 保持（运算结束后随时可读）
    carry_d = c.mux(is_arith, carry, carry64)

    # cout 引脚：算术拍直通本拍组合进位（第 4 拍即可读到最终 cout），
    # 其余拍显示寄存器值（上一次运算的最终进位保持不变）
    cout_pin = c.mux(is_arith, carry, carry64)

    # --- 顶部插入字：LOAD=in_word，ADD/SUB=sum_word，READ=底字（纯旋转） ---
    t = [c.mux(is_arith, bottom[k], sum_word[k]) for k in range(64)]
    new_word = [c.mux(is_load, t[k], in_word[k]) for k in range(64)]

    # --- 移位：acc[k] <= acc[k+64]（k<192，纯连线），acc[192+k] <= new_word[k] ---
    acc_d = [acc[64 + k] for k in range(192)] + new_word

    # --- 输出字：LOAD 回显输入，ADD/SUB 流出和字，READ 流出底字 ---
    out_word = [c.mux(is_load, t[k], in_word[k]) for k in range(64)]

    # --- 闭环 ---
    c.drive_bus(acc, acc_d)
    c.drive_latch(carry, carry_d)

    # --- 输出封存（必须在最后） ---
    c.sealed_outputs = c.seal_outputs(out_word + [cout_pin])
    return c


# ---------------------------------------------------------------------------
# 驱动辅助（测试与链下驱动器共用）
# ---------------------------------------------------------------------------

def word_bits(word: int) -> List[bool]:
    """64 位字 -> 小端 bool 列表"""
    return [bool((word >> k) & 1) for k in range(64)]


def make_input(word: int, op: int, start: bool) -> List[bool]:
    b = word_bits(word)
    b.append(bool(op & 1))
    b.append(bool(op >> 1))
    b.append(bool(start))
    return b


def words_of(x: int) -> List[int]:
    """256 位整数 -> 4 个 64 位字（LSW 先行）"""
    return [(x >> (64 * i)) & ((1 << 64) - 1) for i in range(4)]


def bits_to_word(bits) -> int:
    return sum(int(b) << k for k, b in enumerate(bits))
