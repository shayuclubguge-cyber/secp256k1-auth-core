"""
piso256 —— 256 位并转串位流器（B 操作数喂给 mcore256 LD_B 的桥梁）

功能：4 拍字装入（64 位/拍，LSW 先行），随后 256 拍逐位流出（MSB 先行），
恰好覆盖 mcore256 LD_B 的 256 拍窗口。全程内容自恢复，可无限重复流出。

设计要点（系统级约定，见 docs/ecrecover_auth_design.md）：
  - 无内部 FSM/计数器：纯使能驱动，时序纪律由 ctrl 的全局 4 拍网格保证
  - load_en 断言传 4 拍，in_word 依次给 word0..3（LSW 先行）
  - stream_en 断言传 256 拍，bit_out 依次给 bit255..bit0（MSB 先行）
  - **网格纪律：装入窗口必须 ph0 起对齐，且装入结束到流出开始的间隔
    ≡ 0 (mod 4) 拍**（空闲期环在 rot64，非 4 倍数拍会错位 tap；
    ctrl 的所有传输窗口都在 ph0..3，天然满足）
  - load_en 与 stream_en 不得同时断言（stream 优先，ctrl 保证不交叠）

引脚（66 入 / 1 出）：
  in_word[63:0]  pin 0..63
  load_en        pin 64
  stream_en      pin 65
  bit_out        out 0（R[255] 直通）

装入映射：R[i] = 值 bit i（与 ringbus 一致的全局历元）。
"""

from typing import List

from .. import Circuit, Signal


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_piso256() -> Circuit:
    c = Circuit("piso256", n_in=66, n_out=1)
    pins = c.input_signals()
    in_word = pins[0:64]
    load_en = pins[64]
    stream_en = pins[65]

    R = c.latch_bus(256)

    stream_en_n = c.not_(stream_en)
    load_en_n = c.not_(load_en)

    r_d: List[Signal] = [None] * 256
    for k in range(256):
        rot1 = R[(k - 1) % 256]           # rotl1：内容移向高位，tap=R[255]
        if k < 192:
            # rot64：内容移向低位
            r_d[k] = _mux3(c, R[k + 64], rot1, stream_en, stream_en_n)
        else:
            j = k - 192
            # 非流出拍：load_en 时顶装入 in_word，否则续转 rot64（wrap 源 R[j]）
            hold = _mux3(c, R[j], in_word[j], load_en, load_en_n)
            r_d[k] = _mux3(c, hold, rot1, stream_en, stream_en_n)

    c.drive_bus(R, r_d)

    # 输出：bit_out = R[255]（封存到末尾）
    c.seal_outputs([R[255]])
    return c


# ---------------------------------------------------------------------------
# 驱动辅助（测试与 ctrl 复放共用）
# ---------------------------------------------------------------------------

def load_bits(value: int) -> List[List[bool]]:
    """4 拍装入窗口：word0..3（LSW 先行），load_en=1"""
    seq = []
    for w in range(4):
        word = (value >> (64 * w)) & ((1 << 64) - 1)
        ins = [bool((word >> k) & 1) for k in range(64)]
        ins += [True, False]          # load_en=1, stream_en=0
        seq.append(ins)
    return seq


def stream_bits() -> List[List[bool]]:
    """256 拍流出窗口：stream_en=1，逐拍收 bit_out 即得 MSB..LSB"""
    ins = [False] * 64
    ins += [False, True]
    return [list(ins) for _ in range(256)]


def idle_beats(n: int) -> List[List[bool]]:
    ins = [False] * 66
    return [list(ins) for _ in range(n)]
