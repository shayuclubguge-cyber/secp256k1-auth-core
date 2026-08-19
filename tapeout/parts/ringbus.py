"""
ringbus5 —— 5×256 位字环寄存器堆（ecrecover_auth 的坐标/临时存储）

系统级约定（与 piso256 / mcore256 网格一致）：
  - 环自由旋转 64 位/拍（rot64），任意 4 的倍数拍后内容复原 —— 永恒不丢
  - 写：we=1 + waddr 选中环，连续 4 拍（ph0..3）总线给 word0..3（LSW 先行），
    顶片 [192..255] 装入；写入完成后 R[i] = 值 bit i（全局历元）
  - 读：raddr 选中环，rword = 环低片 [0..63]；**ph==w 拍读到 word w**
    （ctrl 跟踪全局相位，所有 64 位传输都在 ph0 起的 4 拍窗口内完成）
  - 无内部相位计数器：写注入由 we 逐拍驱动，读 tap 固定接线

引脚（71 入 / 64 出）：
  bus[63:0]   pin 0..63    写总线（全环广播）
  waddr[2:0]  pin 64..66   写选择（0..4）
  we          pin 67
  raddr[2:0]  pin 68..70   读选择
  rword[63:0] out 0..63    选中环的低片
"""

from typing import List

from .. import Circuit, Signal

N_RINGS = 5


def _mux3(c: Circuit, a: Signal, b: Signal, sel: Signal, sel_n: Signal) -> Signal:
    """3-NAND MUX（共享反相）：sel=0 选 a，sel=1 选 b"""
    t1 = c.nand(a, sel_n)
    t2 = c.nand(b, sel)
    return c.nand(t1, t2)


def build_ringbus5() -> Circuit:
    c = Circuit("ringbus5", n_in=71, n_out=64)
    pins = c.input_signals()
    bus = pins[0:64]
    waddr = pins[64:67]
    we = pins[67]
    raddr = pins[68:71]

    rings = [c.latch_bus(256) for _ in range(N_RINGS)]

    # --- 写地址译码（one-hot，共享反相） ---
    wa_n = [c.not_(a) for a in waddr]

    def addr_eq(addr: int, bits: List[Signal], bits_n: List[Signal]) -> Signal:
        t = c.one()
        for i in range(3):
            t = c.and_(t, bits[i] if (addr >> i) & 1 else bits_n[i])
        return t

    we_r = [c.and_(we, addr_eq(r, waddr, wa_n)) for r in range(N_RINGS)]
    we_r_n = [c.not_(x) for x in we_r]

    # --- 环：rot64 自由旋转 + 顶片写注入 ---
    for r in range(N_RINGS):
        R = rings[r]
        r_d: List[Signal] = [R[k + 64] for k in range(192)]
        r_d += [_mux3(c, R[j], bus[j], we_r[r], we_r_n[r]) for j in range(64)]
        c.drive_bus(R, r_d)

    # --- 读 mux：5→1，64 位（低片 tap = word(ph)） ---
    ra_n = [c.not_(a) for a in raddr]
    rd_sel = [addr_eq(r, raddr, ra_n) for r in range(N_RINGS)]
    rd_sel_n = [c.not_(x) for x in rd_sel]

    rword: List[Signal] = []
    for k in range(64):
        t = rings[0][k]
        for r in range(1, N_RINGS):
            t = _mux3(c, t, rings[r][k], rd_sel[r], rd_sel_n[r])
        rword.append(t)

    c.seal_outputs(rword)
    return c


# ---------------------------------------------------------------------------
# 驱动辅助（测试与 ctrl 复放共用）
# ---------------------------------------------------------------------------

def write_beats(ring: int, value: int) -> List[List[bool]]:
    """4 拍写窗口（ph0..3 对齐由调用者保证）：bus 给 word0..3"""
    seq = []
    for w in range(4):
        word = (value >> (64 * w)) & ((1 << 64) - 1)
        ins = [bool((word >> k) & 1) for k in range(64)]
        ins += [bool(ring & 1), bool(ring & 2), bool(ring & 4), True]
        ins += [False, False, False]     # raddr=0（读不关心）
        seq.append(ins)
    return seq


def read_input(raddr: int) -> List[bool]:
    ins = [False] * 64
    ins += [False, False, False, False]  # waddr=0, we=0
    ins += [bool(raddr & 1), bool(raddr & 2), bool(raddr & 4)]
    return ins


def idle_input() -> List[bool]:
    return [False] * 71


def word_of(bits) -> int:
    return sum(int(b) << k for k, b in enumerate(bits))
