"""
重新生成流片网表（修正版，输出已封存到电路末尾）。
用法: python scripts/make_netlists.py
输出: vectors/keccak_component.net, vectors/u256_add_mod.net
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_keccak_component import build_keccak_component
from test_u256_math import build_u256_add_mod
from tapeout.parts.fadd64 import build_fadd64


def emit(name, build):
    c, outs = build()
    # 防回归：输出必须恰好是电路最末 n_out 个信号
    expect = list(range(c._next_signal - c.n_out, c._next_signal))
    got = [s.id for s in outs]
    assert got == expect, f"{name}: 输出未封存到末尾"
    blob = c.to_bytes()
    path = ROOT / "vectors" / f"{name}.net"
    path.write_bytes(blob)
    print(f"{name}: {c.n_in}→{c.n_out}, 门数 {c.gate_count():,}, "
          f"{len(blob):,} 字节, sha256 {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"  -> {path}")


def emit_fadd64():
    c = build_fadd64()
    expect = list(range(c._next_signal - c.n_out, c._next_signal))
    got = [s.id for s in c.sealed_outputs]
    assert got == expect, "fadd64: 输出未封存到末尾"
    blob = c.to_bytes()
    path = ROOT / "vectors" / "fadd64.net"
    path.write_bytes(blob)
    n_latch = sum(1 for i in c.instructions if i[0] == 1)
    print(f"fadd64: {c.n_in}→{c.n_out}, 门数 {c.gate_count():,}, LATCH {n_latch}, "
          f"{len(blob):,} 字节, sha256 {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"  -> {path}")


if __name__ == "__main__":
    emit("keccak_component", build_keccak_component)
    emit("u256_add_mod", build_u256_add_mod)
    emit_fadd64()
