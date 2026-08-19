# FairDice Core —— 链上可验证公平骰子（设计文档）

定位：TapeOut 生态第一个「引用即挖矿」示范电路——输入签名，输出骰子，
每一步都引用底层密码学零件。主控壳只做调度，重计算全部交给已链上验证的
ECREC v2 零件（`0xa3b6d9121146c29fb236001b93a45cb7a78a2247`）。

## 1. 主控壳 fairdice_ctrl（74→83，842 NAND + 3 LATCH + 4 REF，6,828B）

sha256 = `95c63687633e1a7d7d5cbb9da91a624bd0ff04cba9ed95e01aa66af8d220e75e`
（`vectors/fairdice_ctrl.net`，源码 `tapeout/parts/fairdice_ctrl.py` 可逐字节重建）

### REF×4 指令（全部指向 ECREC v2，跨处理器引用）

| REF | 目标 | cid | 引脚 | 用途 |
|---|---|---|---|---|
| REF0 | ecrecover_ctrl | 8 | 68→70 | AUTH 签名验证主控（宏指令序列器） |
| REF1 | kl_top | 13 | 65→66 | HASH Keccak-256（缝合 cid7/9/10/11/12） |
| REF2 | fadd64b | 6 | 67→65 | MIX 种子+区块高度（mod 2^256） |
| REF3 | piso256 | 3 | 66→1 | STREAM 位流（MSB 先行）喂 mod-6 累加器 |

嵌套深度：最深 FairDice → ecrecover_ctrl → mcore256 → fadd64 = 3 级
（链上已验证深度为 2，见「已知首次」一节）。

### 引脚分配

输入（74 针）：
- `bus[63:0]` pin 0..63 —— 共享 64 位字总线（全零件一致的 LSW 先行纪律）
- `unit[1:0]` pin 64..65 —— 单元选择：0=AUTH 1=HASH 2=MIX 3=STREAM
- `ctrl[7:0]` pin 66..73 —— 单元本地控制字（未选中单元自动零门控）：
  - unit=0 AUTH：ctrl[0..2]=cmd，ctrl[3]=start（与 ecrecover_ctrl 一致）
  - unit=1 HASH：ctrl[0]=start（与 kl_top 一致）
  - unit=2 MIX：ctrl[0..1]=op，ctrl[2]=start（op: 0=LOAD 1=ADD 2=SUB 3=READ）
  - unit=3 STREAM：ctrl[0]=load_en，ctrl[1]=stream_en，ctrl[2]=acc_clear

输出（83 针）：
- `out_word[63:0]` pin 0..63 —— 按 unit 四选一（STREAM 时 bit0=piso bit_out）
- `status[15:0]` pin 64..79 —— bit0..5 = AUTH 全部 6 个状态
  （busy/done/fail/fadd_cout/mcore_busy/mcore_done，MUL 流程依赖拍级观测），
  bit6/7 = hash busy/done，bit8 = mix cout，bit9 = stream bit_out
- `dice[2:0]` pin 80..82 —— acc+1（二进制 001..110 = 点数 1..6）

### 壳内唯一自有逻辑：mod-6 Horner 累加器（3 LATCH + ~34 NAND）

每拍（stream_en 断言时）：`acc ← (2·acc + bit) mod 6`。
等价推导：t = 2·acc+bit ∈ {0..11}；ge6 = a2 | (a1·a0)；
t≥6 时 acc' = t−6 = (t+2) mod 8 = (a1⊕a0, ¬a0, bit)，否则 (a1, a0, bit)。
acc_clear 清零；dice = acc+1 组合输出。
（测试穷举 0..23 + 2^255/2^256−1 等特值全对。）

## 2. 掷骰流程（驱动器编排，运算原子化）

```
签名 (z, r, s, v) + 区块高度 height
  → AUTH   门级结构校验：NE0 r / NE0 s / LTCHK r<n / LTCHK s<n
           + MUL 锚点 T2 = r·s mod n（回读对拍）
           完整 ECDSA 恢复 = 同一单元跑 compile_ecrecover 宏程序（影子层验证）
  → HASH   seed = Keccak-256(r‖s)（64 字节，恰为 kl_top 原生输入规格）
  → MIX    sum  = (seed + height) mod 2^256（fadd64 LOAD→ADD→READ，进位丢弃）
  → STREAM piso256 流出 sum 的 256 位 → 壳内 mod-6 → dice = acc+1 ∈ 1..6
```

骰子语义定义（任何人可离线复算）：
**dice = ((Keccak256(r‖s) + height) mod 2^256) mod 6 + 1**

公平性：seed 是签名的 Keccak 哈希（签名者不可预选、矿工不可篡改、无服务器）；
height 逐块递增不可回拨；mod 6 偏差 ~2^-256 量级（可忽略）。
seed 随骰子一并输出，即为可验证凭证。

为什么 mod 6 不走 mcore256/fadd64 mod_add：mcore256 约减要求模数 m > 2^255，
fadd64 mod_add 要求操作数已 < m——二者都无法直接对 256 位种子取 mod 6。
「piso256 位流 + 壳内 3 位 Horner 累加器」是符合现有零件语义的唯一路径，
且 piso256 正是 ModMath Core 的 serialize 零件。

## 3. 已知首次（诚实声明）

- **REF 嵌套深度 3**：FairDice → ecrecover_ctrl(cid8) → mcore256(cid2) →
  fadd64(cid1)。链上实测验证过的最深嵌套为 2（nested_ref_probe、
  modmath_ctrl → mcore256 → fadd64）。深度 3 的结构穿透（circuitInfo
  nState 递归汇总）预期成立，流片后第一步用 `circuitInfo(1)` 回读确认。
- 完整 ecrecover 宏程序含 9,487 条 MUL，门级全量约 8 小时/组、
  链上 beat gas 远超 BEP-652 上限——与 ECREC 自身发布同级处理：
  L1 门级 gadget（结构校验 + MUL 锚点）+ L2 编译器影子执行对照黄金模型。

## 4. 验证证据链（2026-08-19 全绿）

- 本地对拍 `tests/test_fairdice_core.py`：
  - 结构约束：74/83 引脚 ≤255；6,828B ≤ 24,575B（OKX 单块）；
    849 指令 ≤ 3,500；晶体管 845 < 3,000；预估流片 gas 2,874,588 ≪ BEP-652
  - mod-6 累加器穷举 + 特值全对
  - **10 组有效签名（不同消息/地址/高度）10/10**：结构校验门级通过、
    MUL 锚点回读一致、恢复地址 == 签名者（影子层）、seed == keccak256(r‖s)、
    dice == 期望值 ∈ 1..6
  - 边界 9 组：r=0 / s=0 / 空输入 / r≥n / s≥n / 双超 n → fail 置位（门级）；
    r=s=n−1 极大合法值全流程正常；r‖s=0xFF×64 + 回绕高度正常取模；
    伪造签名 s+1 → 恢复地址不匹配（应用层检出）
- 一键验证 `scripts/verify_release.py --fairdice-only`：
  依赖闭包 cid 1–13 链上回读 13/13 字节级一致 + 主控壳源码重建=存档 +
  REF×4 指向校验（报告 `docs/FAIRDICE_VERIFY_REPORT.md`）
- 复用零件指纹与 CHAIN_ASSETS.md 链上记录全部一致（9 项交叉验证）

## 5. 发射（待简介定稿后执行）

`scripts/fairdice_launch.py`：precheck → create → mint → tapeout → withdraw → verify

- 晶体管需求：842 NAND + 3 LATCH（mint 总额 0.0845 BNB，withdraw 可回收）
- 净成本预估：≈0.0018 BNB（0.001 deployFee + 2 笔协议费 0.0002 + gas ~0.0006）
- symbol 候选 "DICE"（备选 "FAIR"；与现有处理器无冲突）
- ⚠️ 简介（story）创建后不可改（ModMath v1 教训），必须用户拍板 A/B/C 后填入
