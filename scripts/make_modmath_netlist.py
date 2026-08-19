"""
生成 ModMath Core 主控壳流片网表。
用法: python scripts/make_modmath_netlist.py
输出: vectors/modmath_ctrl.net（+ 指纹打印）
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout.parts.modmath_ctrl import build_modmath_ctrl, DEFAULT_REFS

V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")


def main():
    c = build_modmath_ctrl(V2_CPU, DEFAULT_REFS)
    # 防回归：输出必须恰好是电路最末 n_out 个信号
    expect = list(range(c._next_signal - c.n_out, c._next_signal))
    got = [s.id for s in c.sealed_outputs]
    assert got == expect, "modmath_ctrl: 输出未封存到末尾"
    blob = c.to_bytes()
    nand = sum(1 for i in c.instructions if i[0] == 0)
    latch = sum(1 for i in c.instructions if i[0] == 1)
    ref = sum(1 for i in c.instructions if i[0] == 2)
    path = ROOT / "vectors" / "modmath_ctrl.net"
    path.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    print(f"modmath_ctrl: {c.n_in}→{c.n_out}, NAND {nand}, LATCH {latch}, REF {ref}, "
          f"{len(blob):,} 字节")
    print(f"sha256 = {sha}")
    print(f"REF 目标: fadd64b cid{cids['fadd'] if (cids:=DEFAULT_REFS) else '?'}, "
          f"mcore256 cid{cids['mcore']}, piso256 cid{cids['piso']}, "
          f"ringbusA cid{cids['ring']}  (cpu=0x{V2_CPU.hex()})")
    print(f"  -> {path}")
    assert len(blob) <= 24_575, "超 OKX 单块上限"
    assert c.n_in <= 255 and c.n_out <= 255


if __name__ == "__main__":
    main()
