#!/usr/bin/env python3
"""
链上语义探针电路包生成器

目的：在流片任何时序电路之前，用最小的电路验证链上 witness 合约的
LATCH 语义是否与本地仿真器（两遍语义）一致。

探针 A（对照组，两种语义下行为应一致）：
  1 入 1 出，out = LATCH(d=输入)
  预期：第 k 拍输出 = 第 k-1 拍的输入（延迟一拍）

探针 B（探针，语义分歧点）：
  0 入 1 出，1 位翻转计数器
  结构：LATCH 指令在前（d 前向引用后续 NAND 的输出），NAND 引用 LATCH 旧值
  两遍语义预期：0,1,0,1,0,1,0,1,...
  严格单遍语义：LATCH 执行时 d 引用的信号尚不存在 → 报错 / 恒定输出

产物：
  vectors/probe_a.net, vectors/probe_b.net   协议字节码
  vectors/probe_a.hex, vectors/probe_b.hex   十六进制（便于粘贴）
  vectors/PROBE_GUIDE.md                     链上操作步骤与判定表
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tapeout import Circuit
from tapeout.simulator import Simulator


def build_probe_a():
    """对照组：1入1出，输出=输入延迟一拍"""
    c = Circuit("probe_a_delay", n_in=1, n_out=1)
    d = c.input_signals()[0]
    out = c.latch(d)
    c.buf(out)
    return c


def build_probe_b():
    """探针：0入1出，翻转计数器（LATCH 反馈环 + d 前向引用）"""
    c = Circuit("probe_b_toggle", n_in=0, n_out=1)
    reg = c.latch_reg()           # 占位：LATCH 指令在前
    nxt = c.nand(reg, c.one())    # NOT(reg) —— 引用 LATCH 旧值
    c.drive_latch(reg, nxt)       # d 前向引用后续信号
    c.buf(reg)
    return c


def main():
    outdir = ROOT / "vectors"
    outdir.mkdir(exist_ok=True)

    # ---- 探针 A：本地预期 ----
    ca = build_probe_a()
    sim = Simulator(ca)
    a_inputs = [True, False, True, True, False, False]
    a_expected = [sim.beat([x])[0] for x in a_inputs]

    # ---- 探针 B：本地预期 ----
    cb = build_probe_b()
    sim = Simulator(cb)
    b_expected = [sim.beat([])[0] for _ in range(8)]

    for name, c in [("probe_a", ca), ("probe_b", cb)]:
        blob = c.to_bytes()
        (outdir / f"{name}.net").write_bytes(blob)
        (outdir / f"{name}.hex").write_text("0x" + blob.hex())
        print(f"{name}: {c.gate_count()} 门, {len(c.instructions)} 条指令, "
              f"{len(blob)} 字节, sha256={hashlib.sha256(blob).hexdigest()[:16]}")

    print(f"\n探针A 输入序列:  {[int(x) for x in a_inputs]}")
    print(f"探针A 预期输出:  {[int(x) for x in a_expected]}")
    print(f"探针B 预期输出(8拍): {[int(x) for x in b_expected]}")

    guide = f"""# 链上 LATCH 语义探针操作指南

**目的**：流片时序电路前，验证链上 witness 合约的 LATCH 语义。
**成本**：两个电路共 {ca.gate_count() + cb.gate_count()} 门，十几笔 beat 交易。

## 探针 A（对照组）— probe_a.hex

- 结构：1 入 1 出，`输出 = LATCH(输入)`，{ca.gate_count()} 门
- 操作：部署后连续 beat 6 拍，输入依次为：

| 拍 | 1 | 2 | 3 | 4 | 5 | 6 |
|----|---|---|---|---|---|---|
| 输入 | {[int(x) for x in a_inputs]} |
| 预期输出 | {[int(x) for x in a_expected]} |

- 判定：输出 = 上一拍输入（延迟一拍）→ 链上基本 LATCH 行为正常

## 探针 B（语义探针）— probe_b.hex

- 结构：0 入 1 出，翻转计数器，{cb.gate_count()} 门
  - 指令顺序：`LATCH(d=s3)` 在前，`s3 = NAND(s2, 1)` 在后 —— d **前向引用**后续信号
- 操作：部署后连续 beat 8 拍（无输入）

| 拍 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|----|---|---|---|---|---|---|---|---|
| 两遍语义预期 | {[int(x) for x in b_expected]} |

- 判定：
  - 输出 `0,1,0,1,...` → 链上是两遍语义 ✅ 阶段4时序电路可直接流片
  - 部署/beat 报错回滚 → 链上是严格单遍语义 ❌ 需改用外部反馈架构
  - 输出恒定 0 或 1 → 前向引用被静默读成默认值 ❌ 同上

## 结果回报

把两个探针的实际输出序列发回来，我据此确认架构或调整设计。
"""
    (outdir / "PROBE_GUIDE.md").write_text(guide)
    print(f"\n指南已写入 {outdir}/PROBE_GUIDE.md")


if __name__ == "__main__":
    main()
