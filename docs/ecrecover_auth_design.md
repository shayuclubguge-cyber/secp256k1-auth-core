# ecrecover_auth 顶层设计 v2（2026-08-18）

旗舰核最后一块：把 ecrecover 完整流程（lift_x → 2×标量乘 → 模逆 → 仿射化 → Keccak 地址派生）
做成可流片、可 REF、可链上见证的自包含电路群。

## 0. 硬约束（全部实测，不再复核）

| 约束 | 数值 |
|---|---|
| 单电路字节码 | ≤ 24,575 B（≈3,500 指令；NAND=7B，LATCH=4B，REF=31+3·nIn B） |
| REF 引脚 | ≤255 入 / ≤255 出；REF 零代币消耗 |
| REF 嵌套深度 | ≥2 层已验证（v1 cid11 = probe→cid8→cid7，beat 4/4 全对） |
| 状态穿透 | 子电路 LATCH 计入父状态向量（cid8/cid11 实测） |
| beat gas | ≈2,318 gas/指令 + ~20 万固定开销（fadd64 3,174 指令=736 万/拍实测） |
| OKX 发送上限 | ~30M gas；BSC 协议上限 55M gas（MetaMask 可用满） |

## 1. 核心矛盾与破题

ecrecover 峰值工作集（sR 标量乘阶段）：运行点 X,Y,Z（Jacobian）+ 基点 x,y（仿射）
+ 4 个临时槽 ≈ **9×256 位** + r（模逆原料）= 10 个值。

字环（word-ring）存储成本实测：**每环 256 LATCH + 写入口 192 NAND ≈ 2.4KB**，
10 环 + 端口 ≈ 27KB —— 任何单电路方案都装不下；且「多环合一电路」省不了门
（端口门按环计，不共享）。

**破题：存储按环拆零件，常数走引脚直供。**

1. **寄存器堆 = 2 颗 ringbus5 电路**（各 5 环 + 内部写分发 + 读 mux，不同 cid）。
   同一网表流片两次（若重名被拒则微调死代码）→ 两实例状态天然独立，
   **完全绕开「同 cid 多 REF 实例是否别名」的未测语义**。
2. **常数（p, n, rc_p, rc_n, Gx, Gy, 小常数）与输入（z, r, s, v）不占用寄存器**：
   全部照公开驱动脚本在精确拍从**引脚直流**（s/z 标量位流、LD_C 常数窗）。
   信任注记：网表固定 + 驱动脚本公开确定，任何人可本地复放逐拍比对。
3. **r⁻¹（链上算出的标量）无需专用位环**：ctrl 内置 64 位移位器，
   每 64 次迭代从 ringbus 取回一个字（~260 指令）——bring256 零件取消。
4. **LD_B 位流由 piso256 供给**（字装 4 拍 → rotl1 逐位 256 拍 MSB 先行，
   恰合 mcore256 LD_B 窗口）：964 NAND + 256 LATCH = 7,772B（已验证）。
   装入窗口 ph0 起 + 装流间隔 ≡0(mod 4) 的网格纪律已实测（test_pico_mul 7/7）。
5. **fadd64 必须流片第二份（fadd64b）**：mcore256 内部已 REF v2 cid1，
   ctrl 若再直接 REF 同 cid，加/减会污染 mcore256 的 ACC（别名风险）→
   ctrl 专用独立实例。
6. **Keccak 独立成 keccak_link 小电路**（REF v1 cid4）：地址派生只在收尾发生，
   从主电路摘出可使主电路 beat gas 降 ~5M，且见证链更清晰
   （主电路输出 Qx,Qy 有见证 → keccak_link 输入即被见证值）。

## 2. 架构图

```
ecrecover_ctrl (v2, 新)          ~2,100 NAND + 400 LATCH + 6 REF ≈ 17.5KB
  引脚: 68 入 (in_word64/cmd3/start) / 66 出 (out_word64/busy/done) —— 与 mcore256 同风格
  ├─ REF ringbusA (新, 5环: X, Y, Z, T1, r)      1,942 NAND + 1,280 LATCH = 18,714B（已验证）
  ├─ REF ringbusB (新, 5环: bx, by, T2, T3, T4)  同网表/变体, 不同 cid
  ├─ REF piso256  (新, B 操作数位流器)            964 NAND + 256 LATCH = 7,772B（已验证）
    ├─ REF fadd64b  (新 = fadd64 网表二流, 独立 cid) 2,917 NAND + 257 LATCH ≈ 21.4KB
  └─ REF mcore256 (v2 cid2 已有)                  2,339 NAND + 1,041 LATCH
        └─ REF fadd64 (v2 cid1 已有)              ← 深度 2，已验证 ✓

keccak_link (v2, 新, 独立小电路)   REF v1 cid4 + 1,600 位状态环 ≈ 8-10KB
  引脚字流: Qx‖Qy (64B) 流入 → 20B 地址 + valid 流出
```

最大 REF 深度 = 2（ctrl→mcore256→fadd64），已被 cid11 探针覆盖，**无新嵌套风险**。

## 3. 微流程（FSM 骨架）

| 阶段 | 内容 | 模乘数 | 备注 |
|---|---|---|---|
| LOAD | v 锁存；r→ring | 0 | z/s 不存（届时引脚流） |
| lift_x | y2=x³+7 → T1；y=√y2（费马 (p+1)/4）；校验 y²=y2；奇偶修正 | ~390 | 指数位由 FSM ROM 硬编码 |
| invmod_n | rinv = r^(n-2) mod n → bring256 | ~510 | p/n 常数窗切换 |
| sR | 256 迭代：倍点(8乘)+按 s 位加点(11乘) | ~3,470 | s 位引脚流；基点 (x,y) |
| zG | 同上，基点 (Gx,Gy) 常数直供 | ~3,470 | 复用全部数据通路 |
| diff | sR + (−zG)（一次加点 + Y 取负）→ 仿射化 | ~520 | 含 1 次 invmod_p |
| Q | rinv·diff 标量乘（bring256 供位） | ~3,470 | |
| 仿射 | zinv=invmod_p(Z)；Qx,Qy 输出见证 | ~515 | |
| **合计** | | **~12,700 乘** | **≈ 33M 拍** |

加/减 ~1.5 万次 × ~12 拍 ≈ 0.2M 拍（fadd64b，可忽略）。
本地复放：JS Worker ~6-10 分钟（演示站路径已验证）；Python 仅做单元级。

## 4. 预算总览

| 项 | 数值 | 说明 |
|---|---|---|
| 新流片电路 | 6 颗 | ringbusA/B, piso256, fadd64b, ctrl, keccak_link |
| v2 代币消耗 | ~9,700 NAND + ~3,600 LATCH | ≈1.33 BNB 自铸（可回收），gas ~35M ≈ 0.0018 BNB |
| 单拍 beat gas | ~46M | 超 OKX 30M → **链上见证走 MetaMask**（<55M ✓） |
| 全链拍数 | ~33M 拍 | 链上只做关键 I/O 见证，全量走本地复放 |
| 单电路字节码 | 全部 ≤21.4KB | 最大件 fadd64b（已实测可发） |

## 5. 验证阶梯

1. 零件级 Python SimWithREF 单测（ringbus/piso/bring 各自 + 组合）
2. ctrl + 全 REF 树本地跑 demo 向量（msg="hello tapeout"）对拍 ethers.js
3. v2 流片：每颗流片后 netlist(cid) sha256 指纹校验
4. 链上冒烟：LOAD + 前几次模乘 beat 见证（MetaMask），与本地逐拍比对
5. 演示站接入：全链复放 + 地址派生 + 指纹校验

## 6. 上链顺序

1. ~~piso256 / ringbus5~~（已本地全绿：单测 + 集成冒烟）
2. ringbusA/B 流片（同网表双实例）
3. fadd64b（二流，纯流程复用）
4. ecrecover_ctrl（主战场）
5. keccak_link + 演示站收尾
