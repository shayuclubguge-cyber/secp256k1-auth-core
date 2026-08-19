"""
alu_driver —— fadd64（cid10）的驱动器与高层模运算序列

架构决策（2026-08-18）：
  fadd64 只有一个 ACC。乘法所需的操作数 A 翻倍（A=2A mod p）由驱动器在链下
  维护（值域 <2^256 的余数不变式：ACC ≡ R (mod m)），电路内不复制操作数寄存器。
  所有进入最终结果的 256 位模运算都流经 fadd64 ACC，op 序列完整可复放：
  任何人对照链上网表重放 op 流即可验证结果。

不变式：任意时刻 ACC < 2^256 且 ACC ≡ R (mod m)。
  - ADD 溢出（cout=1）→ 立即 ADD R_c（R_c = 2^256 - m）补偿，恢复 <2^256
  - 全程无需中途减 m（p,n 都 ≈ 2^256，<2^256 的值至多超 m 一次）
  - 收尾一次 SUB m +（若借位）ADD m 即得规范余数

拍数预算（mod-p 乘法）：256 位 × [b_i=1 时 ADD(4拍) + 溢出补偿 ~50%(4拍)]
  ≈ 256×(2+1) = 768~1,536 拍/次
"""

from typing import List, Optional, Tuple

from ..field import P, N
from ..simulator import Simulator
from .fadd64 import build_fadd64, make_input, words_of, bits_to_word, OP_LOAD, OP_ADD, OP_SUB, OP_READ

MASK = (1 << 256) - 1
RC_P = (1 << 256) - P        # 2^32 + 977
RC_N = (1 << 256) - N


class AluDriver:
    """fadd64 驱动器：可同时驱动本地仿真器（未来：链上 beat 复放同一 op 流）"""

    def __init__(self, sim: Optional[Simulator] = None):
        self.sim = sim or Simulator(build_fadd64())
        self.trace: List[Tuple[int, int]] = []   # (op, word) 流，供复放/审计

    def _beat(self, word: int, op: int, start: bool) -> Tuple[int, bool]:
        out = self.sim.beat(make_input(word, op, start))
        return bits_to_word(out[:64]), bool(out[64])

    def op(self, op: int, x: int) -> Tuple[List[int], bool]:
        """4 拍流一个 256 位操作数，返回 (out_words, 最终cout)"""
        outs, cout = [], False
        for i, w in enumerate(words_of(x)):
            self.trace.append((op, w))
            ow, cout = self._beat(w, op, i == 0)
            outs.append(ow)
        return outs, cout

    def load(self, a: int):
        self.op(OP_LOAD, a)

    def add(self, b: int) -> bool:
        _, cout = self.op(OP_ADD, b)
        return cout

    def sub(self, b: int) -> bool:
        """返回是否借位（a<b）"""
        _, cout = self.op(OP_SUB, b)
        return not cout

    def read(self) -> int:
        outs, _ = self.op(OP_READ, 0)
        return sum(w << (64 * i) for i, w in enumerate(outs))

    # --- 带不变式维护的模运算 ---

    def add_keep(self, b: int, rc: int):
        """ACC += b，若溢出则补 R_c，维持 ACC<2^256 且 ≡ (mod m)"""
        if self.add(b):
            self.add(rc)

    def reduce_final(self, m: int) -> int:
        """收尾约减：SUB m，若借位则 ADD m 还原。返回规范余数"""
        if self.sub(m):
            self.add(m)
        return self.read()

    # --- 乘法（操作数翻倍链下维护） ---

    def mulmod(self, a: int, b: int, m: int, rc: int) -> int:
        """R = a*b mod m。a 的翻倍在链下维护，恒保持 A < m
        （补偿边界：A<m 时 2A 溢出补偿后为 2A-m < m，恰好完成约减）"""
        self.load(0)
        A = a % m
        for i in range(256):
            if (b >> i) & 1:
                self.add_keep(A, rc)
            A <<= 1
            if A >> 256:
                A = (A & MASK) + rc   # = 2A - 2^256 + rc = 2A - m < m（因 A<m）
            elif A >= m:
                A -= m
        return self.reduce_final(m)

    def mulmod_p(self, a: int, b: int) -> int:
        return self.mulmod(a, b, P, RC_P)

    def mulmod_n(self, a: int, b: int) -> int:
        return self.mulmod(a, b, N, RC_N)

    def addmod_p(self, a: int, b: int) -> int:
        self.load(a)
        self.add_keep(b, RC_P)
        return self.reduce_final(P)

    def submod_p(self, a: int, b: int) -> int:
        self.load(a)
        if self.sub(b):
            self.add(P)
        return self.reduce_final(P)
