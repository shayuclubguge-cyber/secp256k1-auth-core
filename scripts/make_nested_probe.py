#!/usr/bin/env python3
"""
嵌套 REF 探针生成器 —— 顶层 ecrecover_auth 架构的前置验证

目标：验证链上是否支持 **两级嵌套 REF**
  probe_nested (cid11, 待流片)
    └─ REF → cid8 ref_stateful_probe = REF(v1, cid7) + 输出缓冲
       └─ REF → cid7 ctr8 计数器（8 LATCH 有状态）

链上若成立（circuitInfo nState=8 穿透 + beat 计数行为与本地一致），
顶层 ecrecover_auth 的「FSM → REF mcore256 → REF fadd64」架构即解锁。

产物：
  vectors/nested_ref_probe.net / .hex   协议字节码 + 十六进制
  本地 SimWithREF 对拍预期序列（供链上 beat 对照）
"""

import hashlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout import Circuit
from tapeout.simulator import Simulator, SimWithREF, REFResolver

# v1 免费版处理器（cid7/cid8 所在）
V1_CPU = bytes.fromhex("3bceffdf5541d856b23af18e5e26141b90d47289")


def load_net(path: str, name: str, n_in: int, n_out: int) -> Circuit:
    """把链上同款字节码原样解析回 Circuit（保证本地仿真 = 链上网表）"""
    blob = Path(path).read_bytes()
    c = Circuit(name, n_in=n_in, n_out=n_out)
    i = 0
    while i < len(blob):
        op = blob[i]; i += 1
        if op == 0:
            a = int.from_bytes(blob[i:i+3], "big")
            b = int.from_bytes(blob[i+3:i+6], "big"); i += 6
            c.instructions.append((0, a, b))
            c._next_signal += 1
        elif op == 1:
            d = int.from_bytes(blob[i:i+3], "big"); i += 3
            c.instructions.append((1, d))
            c._next_signal += 1
        elif op == 2:
            cpu = blob[i:i+20]
            cid = struct.unpack(">Q", blob[i+20:i+28])[0]
            nin, nout = blob[i+28], blob[i+29]; i += 30
            ins = [int.from_bytes(blob[i+3*k:i+3*k+3], "big")
                   for k in range(nin)]; i += 3 * nin
            c.instructions.append((2, cpu, cid, nin, nout, ins))
            c._next_signal += nout
        else:
            raise ValueError(f"未知 opcode {op} @ {i-1}")
    return c


def build_nested_probe() -> Circuit:
    """1入8出：REF(v1, cid8) + 两阶段输出缓冲（16 NAND，与 cid8 同款风格）"""
    c = Circuit("nested_ref_probe", n_in=1, n_out=8)
    en = c.input_signals()[0]
    outs = c.ref(V1_CPU, 8, 1, 8, [en])
    c.seal_outputs(outs)
    return c


def bits_to_int(bits):
    """输出位 -> 整数（bit0 为最低位，与 ctr8 计数器语义对照用）"""
    return sum(int(b) << i for i, b in enumerate(bits))


def main():
    # 1) 原样重建链上 cid7 / cid8
    ctr8 = load_net(ROOT / "vectors" / "ctr8.net", "ctr8", 1, 8)
    refsp = load_net(ROOT / "vectors" / "ref_stateful_probe.net",
                     "ref_stateful_probe", 1, 8)
    print(f"cid7 ctr8:              {ctr8.gate_count()} NAND, "
          f"{sum(1 for x in ctr8.instructions if x[0]==1)} LATCH")
    print(f"cid8 ref_stateful_probe: {refsp.gate_count()} NAND + REF→cid7")

    # 2) 单层基线：直接仿 ctr8（链上 beat 已验证 0→1→2）
    enables = [1, 1, 1, 0, 1, 1, 0, 1]
    sim0 = Simulator(ctr8)
    base = [sim0.beat([bool(x)]) for x in enables]
    print(f"\nctr8 直仿 {len(enables)} 拍（en={enables}）:")
    for k, o in enumerate(base):
        print(f"  拍{k}: {[int(b) for b in o]}  (as int: {bits_to_int(o)})")

    # 3) 嵌套仿真：probe_nested → cid8 → cid7
    probe = build_nested_probe()
    resolver = REFResolver()
    resolver.register(V1_CPU, 7, ctr8)
    resolver.register(V1_CPU, 8, refsp)
    sim = SimWithREF(probe, resolver)
    nested = [sim.beat([bool(x)]) for x in enables]
    print(f"\nprobe_nested 嵌套仿真 {len(enables)} 拍:")
    ok = True
    for k, (o, b) in enumerate(zip(nested, base)):
        match = "✓" if o == b else "✗"
        if o != b:
            ok = False
        print(f"  拍{k}: {[int(x) for x in o]} {match}")
    assert ok, "嵌套仿真与 ctr8 直仿不一致！"
    print("嵌套 REF 本地递归仿真：全部一致 ✓")

    # 4) 导出流片产物
    blob = probe.to_bytes()
    (ROOT / "vectors" / "nested_ref_probe.net").write_bytes(blob)
    (ROOT / "vectors" / "nested_ref_probe.hex").write_text("0x" + blob.hex())
    print(f"\nprobe_nested: {probe.gate_count()} NAND, "
          f"{len(probe.instructions)} 指令, {len(blob)} 字节")
    print(f"sha256 = {hashlib.sha256(blob).hexdigest()}")
    print(f"产物: vectors/nested_ref_probe.net / .hex")


if __name__ == "__main__":
    main()
