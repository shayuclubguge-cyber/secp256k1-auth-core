"""
生成 FairDice Core 主控壳流片网表。
用法: python scripts/make_fairdice_netlist.py
输出: vectors/fairdice_ctrl.net（+ 指纹打印）
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout.parts.fairdice_ctrl import build_fairdice_ctrl, DEFAULT_REFS

V2_CPU = bytes.fromhex("a3b6d9121146c29fb236001b93a45cb7a78a2247")


def main():
    c = build_fairdice_ctrl(V2_CPU, DEFAULT_REFS)
    # 防回归：输出必须恰好是电路最末 n_out 个信号
    expect = list(range(c._next_signal - c.n_out, c._next_signal))
    got = [s.id for s in c.sealed_outputs]
    assert got == expect, "fairdice_ctrl: 输出未封存到末尾"
    blob = c.to_bytes()
    nand = sum(1 for i in c.instructions if i[0] == 0)
    latch = sum(1 for i in c.instructions if i[0] == 1)
    ref = sum(1 for i in c.instructions if i[0] == 2)
    path = ROOT / "vectors" / "fairdice_ctrl.net"
    path.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    print(f"fairdice_ctrl: {c.n_in}→{c.n_out}, NAND {nand}, LATCH {latch}, "
          f"REF {ref}, {len(c.instructions)} 指令, {len(blob):,} 字节")
    print(f"sha256 = {sha}")
    print(f"REF 目标: ecrecover_ctrl cid{DEFAULT_REFS['auth']}, "
          f"kl_top cid{DEFAULT_REFS['hash']}, "
          f"fadd64b cid{DEFAULT_REFS['mix']}, "
          f"piso256 cid{DEFAULT_REFS['stream']}  (cpu=0x{V2_CPU.hex()})")
    print(f"  -> {path}")
    assert len(blob) <= 24_575, "超 OKX 单块上限"
    assert len(blob) <= 39_000, "超 BEP-652 ~39KB 上限"
    assert c.n_in <= 255 and c.n_out <= 255
    assert nand + latch < 3_000, "超 3,000 晶体管目标"


if __name__ == "__main__":
    main()
