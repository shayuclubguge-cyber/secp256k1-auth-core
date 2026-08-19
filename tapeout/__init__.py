"""
TapeOut 网表生成器与本地仿真器
tapeout.net 协议字节格式实现
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Tuple


# 信号常量
SIG_ZERO = 0
SIG_ONE = 1


class Signal:
    """电路中的信号（线网）"""
    __slots__ = ("id",)

    def __init__(self, id: int):
        self.id = id

    def __repr__(self):
        if self.id == SIG_ZERO:
            return "SIG_ZERO"
        if self.id == SIG_ONE:
            return "SIG_ONE"
        return f"s{self.id}"


@dataclass
class Circuit:
    """一个电路（网表）"""
    name: str
    n_in: int
    n_out: int
    instructions: List[Tuple[int, ...]] = field(default_factory=list)
    # 信号计数器：0=0, 1=1, 2..n_in+1 是输入，之后每条指令产生一个新信号
    _next_signal: int = field(default=0, repr=False)

    def __post_init__(self):
        self._next_signal = 2 + self.n_in
        # 占位LATCH槽位：信号ID -> 指令对象（d 可后续用 drive_latch 修补）
        self._latch_slots = {}

    # --- 便捷工厂 ---
    def input_signals(self) -> List[Signal]:
        """返回输入引脚信号列表"""
        return [Signal(i) for i in range(2, 2 + self.n_in)]

    def zero(self) -> Signal:
        return Signal(SIG_ZERO)

    def one(self) -> Signal:
        return Signal(SIG_ONE)

    # --- 指令级原语 ---
    def nand(self, a: Signal, b: Signal) -> Signal:
        """NAND 门: out = NOT(a AND b)"""
        self.instructions.append((0, a.id, b.id))
        out = Signal(self._next_signal)
        self._next_signal += 1
        return out

    def latch(self, d: Signal) -> Signal:
        """LATCH: 心跳时读旧状态、写新状态"""
        self.instructions.append((1, d.id))
        out = Signal(self._next_signal)
        self._next_signal += 1
        return out

    def latch_reg(self) -> Signal:
        """
        占位寄存器：立即分配 LATCH 输出信号（占住信号ID与指令位置），
        数据输入 d 稍后用 drive_latch() 修补。

        这是构建寄存器反馈环的标准模式：
          1. regs = [c.latch_reg() for _ in range(n)]   # 先占位
          2. 组合逻辑引用 regs（读到的是上一拍的旧值）
          3. c.drive_latch(regs[i], new_value)          # 闭环
        """
        inst = [1, SIG_ZERO]  # 列表形式，d 可修补
        self.instructions.append(inst)
        out = Signal(self._next_signal)
        self._next_signal += 1
        self._latch_slots[out.id] = inst
        return out

    def drive_latch(self, reg: Signal, d: Signal):
        """修补占位 LATCH 的数据输入（见 latch_reg）"""
        inst = self._latch_slots[reg.id]
        assert inst[0] == 1, f"信号 {reg.id} 不是占位LATCH"
        inst[1] = d.id

    def latch_bus(self, n: int) -> List[Signal]:
        """一次创建 n 位占位寄存器总线"""
        return [self.latch_reg() for _ in range(n)]

    def drive_bus(self, regs: List[Signal], ds: List[Signal]):
        assert len(regs) == len(ds)
        for r, d in zip(regs, ds):
            self.drive_latch(r, d)

    def ref(self, cpu_addr: bytes, circuit_id: int, n_in: int, n_out: int,
            inputs: List[Signal]) -> List[Signal]:
        """
        REF 指令: 引用外部电路
        cpu_addr: 20 字节地址
        circuit_id: u64
        n_in, n_out: 输入/输出数（必须 ≤255）
        inputs: 输入信号列表，长度必须等于 n_in
        """
        assert len(cpu_addr) == 20, "cpu_addr 必须是 20 字节"
        assert len(inputs) == n_in, f"输入信号数 {len(inputs)} != n_in {n_in}"
        assert n_in <= 255 and n_out <= 255, "REF 输入/输出数必须 ≤255"
        self.instructions.append(
            (2, cpu_addr, circuit_id, n_in, n_out, [s.id for s in inputs])
        )
        outs = [Signal(self._next_signal + i) for i in range(n_out)]
        self._next_signal += n_out
        return outs

    def buf(self, a: Signal) -> Signal:
        """缓冲器（2 NAND）：确保信号位于电路末尾，不改变值"""
        t = self.nand(a, a)       # NOT(a)
        return self.nand(t, t)    # NOT(NOT(a)) = a

    def seal_outputs(self, signals: List[Signal]) -> List[Signal]:
        """
        输出封存：协议规定「输出 = 最后 nOut 个信号」。
        两阶段缓冲（先全部 NOT，再全部 NOT），保证给定信号按序占据
        电路的最后 len(signals) 个信号位。流片前必须调用。
        返回封存后的输出信号（即最后 nOut 个信号，可直接验证）。
        """
        ts = [self.nand(s, s) for s in signals]      # 一批 NOT
        return [self.nand(t, t) for t in ts]         # 再一批 NOT = 原值，且在最末尾

    # --- 派生门 ---
    def not_(self, a: Signal) -> Signal:
        """NOT = NAND(a, 1)"""
        return self.nand(a, self.one())

    def and_(self, a: Signal, b: Signal) -> Signal:
        """AND = NOT(NAND(a,b))"""
        return self.not_(self.nand(a, b))

    def or_(self, a: Signal, b: Signal) -> Signal:
        """OR = NAND(NOT(a), NOT(b))"""
        return self.nand(self.not_(a), self.not_(b))

    def xor(self, a: Signal, b: Signal) -> Signal:
        """XOR —— 4 NAND 优化实现"""
        t1 = self.nand(a, b)
        t2 = self.nand(a, t1)
        t3 = self.nand(b, t1)
        return self.nand(t2, t3)

    def mux(self, sel: Signal, a: Signal, b: Signal) -> Signal:
        """二选一 MUX: sel=0 选 a, sel=1 选 b"""
        # out = (a AND NOT(sel)) OR (b AND sel)
        ns = self.not_(sel)
        t1 = self.and_(a, ns)
        t2 = self.and_(b, sel)
        return self.or_(t1, t2)

    def adder(self, a: Signal, b: Signal, cin: Signal) -> Tuple[Signal, Signal]:
        """全加器: 返回 (sum, cout)
        sum  = a xor b xor cin
        cout = majority(a, b, cin)
        注意：不加缓冲器。输出归位由流片前的 seal_outputs 统一保证，
        中途缓冲纯属浪费门数（每全加器 4 门，加法器省电路上千门）。
        """
        s1 = self.xor(a, b)
        sum_ = self.xor(s1, cin)
        t1 = self.and_(a, b)
        t2 = self.and_(cin, s1)
        cout = self.or_(t1, t2)
        return sum_, cout

    # --- 字节码输出 ---
    def to_bytes(self) -> bytes:
        """
        序列化为协议网表字节格式。
        注意：信号编号为 **大端 u24**（与链上合约校验/创世电路网表一致，
        2026-08-18 经 tapeout 合约模拟验证）。
        """
        buf = bytearray()
        for inst in self.instructions:
            op = inst[0]
            if op == 0:  # NAND
                _, a, b = inst
                buf.append(0)
                buf += a.to_bytes(4, "big")[1:]
                buf += b.to_bytes(4, "big")[1:]
            elif op == 1:  # LATCH
                _, d = inst
                buf.append(1)
                buf += d.to_bytes(4, "big")[1:]
            elif op == 2:  # REF
                _, cpu_addr, circuit_id, n_in, n_out, inputs = inst
                buf.append(2)
                buf += cpu_addr
                buf += struct.pack(">Q", circuit_id)
                buf.append(n_in)
                buf.append(n_out)
                for inp in inputs:
                    buf += inp.to_bytes(4, "big")[1:]
            else:
                raise ValueError(f"未知指令类型: {op}")
        return bytes(buf)

    def gate_count(self) -> int:
        """估算门数（NAND + REF 视为引用不计入）"""
        return sum(1 for inst in self.instructions if inst[0] == 0)

    def __repr__(self):
        return (f"Circuit('{self.name}', in={self.n_in}, out={self.n_out}, "
                f"gates={self.gate_count()}, insts={len(self.instructions)})")


# --- 老橘子 Keccak 零件库地址（BNB Chain） ---
ORANGE_SHA256_CPU = bytes.fromhex(
    "99ede0cc500bf7d762d63c5a5152b94216083137"
)
ORANGE_KECCAK_CPU = bytes.fromhex(
    "ec9C5A1602E92Db89E3fb61652019fb3AC30d075"
)

# 零件库电路 ID
XOR3_CID = 20      # 3×64 → 64, 640 门
XOR2_CID = 21      # 2×64 → 64, 384 门
CHI_CID = 22       # χ 通道, 576 门
BUF_CID = 23       # 缓冲/布线辅助, 384 门
RC_ROM_CID = 24    # 轮常数 ROM, 245 门

# Keccak 轮函数（无法 REF，只能 eval）
KECCAK_THETA_RHO_PI_CID = 1   # 1600→1600, 17,920 门
KECCAK_CHI_IOTA_CID = 2       # 1605→1600, 15,029 门
