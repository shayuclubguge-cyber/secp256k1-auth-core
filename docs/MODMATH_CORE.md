# ModMath Core —— 主控壳设计与流片上链清单

> 通用密码学运算库（General Cryptographic Arithmetic Library）
> TapeOut 生态第一个独立的密码学数学基础设施处理器
>
> ✅ **已于 2026-08-19 晚上线 BSC 主网**：Circuits `0x9b30f16fb5f0c91343e678d94670a0d8b231a40d`，
> 主控壳 cid1，流片 tx `0xf9bdd26d2a33c393eeb5160af7246b77465db535ed4e663de0b1d053dcd9b187`，
> 链上回读与本地逐字节一致，v2 净成本 0.001678 BNB。
> （下文第三节为流片前的操作清单存档，已按此执行完毕。）

---

## 一、主控壳网表设计（modmath_ctrl）

### 1.1 设计形态

**纯组合调度壳：771 NAND + 0 LATCH + 4 REF，775 指令，6,337 字节。**

- 所有状态寄宿在 4 颗底层零件内（经 REF 穿透，预计 nState = 3,091 位）
- 壳自身零寄存器 → 无相位计数器、无 FSM，全局 4 拍网格由驱动器 beat 计数维护
- 网表指纹（byte fingerprint）：
  `sha256 = 4767118eb724134a6964249b0327630e7eb609586cc1299bcc86e3773647b13b`
- 存档：`vectors/modmath_ctrl.net`（与源码 `tapeout/parts/modmath_ctrl.py` 现场重建逐字节一致）

### 1.2 REF 指令（4 条，全部指向 ECREC v2 已链上验证零件）

| REF | 目标 | cid | 用途 | 链上指纹前缀 | 状态 |
|---|---|---|---|---|---|
| REF0 | fadd64b | 6 | `mod_add` / `mod_sub` | c22f8c74b0def33c | ✓ 回读一致 |
| REF1 | mcore256 | 2 | `mod_mul`（内部再 REF cid1，深度 2 已链上验证） | 37eb121673dbf830 | ✓ 回读一致 |
| REF2 | piso256 | 3 | `serialize` | 356cdf33ca964192 | ✓ 回读一致 |
| REF3 | ringbusA | 4 | `bus_transfer` / 寄存器堆 | 83beeeb0b50e4bae | ✓ 回读一致 |

**为什么 mod_add 指 cid6（fadd64b）而不是 cid1：** mcore256 内部 REF 的就是
cid1；同 cid 链上状态共享会让 mod_mul 的 RUN 与挂起的 mod_add 互相污染 ACC。
这复刻 ECREC 旗舰主控（cid8 ecrecover_ctrl）已验证的先例——顶层 ALU 用第二实例。
若驱动保证运算序列原子化（先 LOAD 后算），改指 cid1 亦可（构造函数参数可配）。
ringbusB（cid5）为 ringbusA 的同构备用实例。

### 1.3 引脚分配（74 入 / 72 出，全部 ≤255 ✓）

**输入（74 针）：**

| 引脚 | 名称 | 说明 |
|---|---|---|
| 0..63 | `bus[63:0]` | 共享 64 位字总线（全零件一致的 LSW 先行；mcore LD_B 位流走 bus[0]，MSB 先行） |
| 64..65 | `unit[1:0]` | 单元选择：0=mod_add · 1=mod_mul · 2=serialize · 3=bus_transfer |
| 66..73 | `ctrl[7:0]` | 单元本地控制字（未选中单元硬件零门控） |

ctrl 编码（按 unit）：

| unit | ctrl 位 | 含义 |
|---|---|---|
| 0 fadd | [0]=op0 [1]=op1 [2]=start | op: 0=LOAD 1=ADD 2=SUB 3=READ（start=4 拍组首拍） |
| 1 mcore | [0..2]=cmd [3]=start | cmd: 0 LD_A · 1 LD_B · 2 LD_C · 3 RUN · 4 READ · 5 REDUCE |
| 2 piso | [0]=load_en [1]=stream_en | 装入 4 拍 / 流出 256 拍 |
| 3 ring | [0..2]=waddr [3]=we [4..6]=raddr | 5 环寄存器堆写/读 |

**输出（72 针）：**

| 引脚 | 名称 | 说明 |
|---|---|---|
| 0..63 | `out_word[63:0]` | 按 unit 四选一：fadd out_word / mcore out_word / piso{bit0=bit_out, 余 0} / ringbus rword |
| 64..71 | `status[7:0]` | bit0=fadd cout（unit=0）；bit1=mcore busy、bit2=mcore done（unit=1）；bit3..7 恒 0 保留 |

### 1.4 安全性（未选中单元不受垃圾总线干扰）

- fadd 未选中时 op 强制 READ（纯旋转，4 拍自复原）——杜绝误 LOAD 污染 ACC
- mcore 未选中时 start=0（非 IDLE 态不接受命令，in_word 被忽略）
- piso 未选中时双使能归零（空闲 rot64 自恢复）
- ringbus 未选中时 we=0、raddr=0（只读环 0，不写）

### 1.5 标准化运算接口（驱动协议）

```
mod_add(a, b, m):   LOAD a → ADD b → [cout=1: ADD rc] → SUB m → [借位: ADD m] → READ
                    （rc = 2^256 - m；任意 256 位模数）
mod_mul(a, b, m):   LD_C(rc) → LD_A(a) → LD_B(b) → RUN → LD_C(m) → REDUCE → READ
                    （要求 m > 2^255；~1,600–2,900 拍/次）
serialize(data):    ph0 对齐 → load_en×4 拍（字 LSW 先行）→ stream_en×256 拍（位 MSB 先行）
bus_transfer(s,d):  ph0 对齐 → 读环 s×4 拍 → 写环 d×4 拍（共享总线转运）
```

驱动器实现：`tapeout/parts/modmath_ctrl.py::ModMathDriver`（本地对拍与链上
beat 复放共用同一拍序列）。

---

## 二、本地对拍测试报告（全绿）

脚本：`tests/test_modmath_core.py`（一键：`python scripts/verify_release.py --modmath-only`）
完整报告：`docs/MODMATH_VERIFY_REPORT.md`

| 组别 | 向量数 | 结果 | 说明 |
|---|---|---|---|
| [1] mod-p 模乘 | 14/14 | ✓ | 复用 ECREC 已通过标准（同向量同种子 20260818） |
| [2] mod-n 模乘 | 5/5 | ✓ | 复用 ECREC 已通过标准（同种子 777） |
| [3] 通用素数域 | 5/5 | ✓ | secp256r1 p/n、brainpoolP256r1 p、2^256−189、2^255+95（首个 >2^255 素数），均过 Miller-Rabin |
| [4] 并转串 serialize | 3/3 | ✓ | 固定 + 全 1 + 随机，256 位 MSB 先行 |
| [5] 总线传输 | 2/2 | ✓ | 环间拷贝（源保持+邻环隔离）+ 跨组件 mcore→ringbus 转运 |
| [附] mod_add / mod_sub | 12 + 4 | ✓ | 主控壳 unit=0 通路（mod-p ×8 + mod-n ×4 + sub ×4） |
| [附] REF 状态隔离 | ✓ | ✓ | mod_mul 后 mod_add 不污染（cid6/cid1 实例独立） |
| [附] 字节指纹交叉验证 | 4/4 | ✓ | 4 颗复用零件与链上记录指纹一致、与 vectors/ 存档逐字节一致 |

结构约束：74→72 引脚 ≤255 ✓ · 6,337B ≤ 24,575B（OKX 单块）✓ ·
775 指令 ≤ 3,500 ✓ · 预估流片 gas 2,667,877 ≪ BEP-652（16,777,216）✓ ·
逻辑晶体管总量（壳 + 4 零件）11,767 ∈ [8,000, 12,000] ✓

**合计 29 组任务书向量（14+5+5+3+2）+ 16 组附加向量，100% 一致。**

---

## 三、流片上链操作清单

### 3.1 晶体管需求

| 项 | 数量 | 说明 |
|---|---|---|
| 主控壳自有 | **771 NAND + 0 LATCH** | 流片实际烧铸量 |
| 4 颗零件引用 | 0 | REF 零消耗（v1 cid5/cid6 探针已证实，跨处理器同样免费） |
| 网表字节 | 6,337 B | 单块 SSTORE2 内（≤24,575B） |
| 预估流片 gas | ~2.7M | 421 × 字节数；≪ BEP-652 上限 |

### 3.2 预估成本（自铸资金闭环：mint→withdraw 同钱复用，净成本仅协议费+gas）

| 路径 | 明细 | 净成本 |
|---|---|---|
| **A. 新建独立处理器「ModMath Core」**（推荐，对标定位） | 创建 CPU deployFee 0.001 + mint 771N 协议费 0.0001 + 流片 gas ~0.0004 + withdraw gas ~0.00001 | **~0.0015 BNB** |
| B. 直接上流 ECREC v2（作为 cid14，同处理器 REF） | mint 771N 协议费 + 流片 gas | ~0.0006 BNB |

均在预算 0.001–0.002 BNB 内。新处理器建议参数：supply=1,000,000（合并封顶）、
mintPrice=0.0001 BNB（对标 ECREC v2 与老橘子 KECCAK 库）。

### 3.3 操作步骤

1. **创建处理器**（路径 A）：TapeOut 官网 → 创建 CPU → name `ModMath Core`、
   symbol `MMAT`、supply 1,000,000、mintPrice 0.0001 BNB → 记录 Circuits /
   Transistors 合约地址与创建 tx
2. **铸币**：新处理器 Transistors 合约 mint(id=0, 771 NAND)，value=771×0.0001+0.0001
3. **流片**：画布加载 `vectors/modmath_ctrl.net`（或逐指令构造），确认 4 条 REF
   指向 ECREC v2 `0xa3b6d912…47` 的 cid 6/2/3/4 → 点「流片上链」
   （MetaMask + `bsc-dataseed.binance.org`，gasPrice 0.15 gwei，显式 gas ~3M）
4. **记录**：交易哈希、cid（新处理器内从 1 开始）、创建时间
5. **注册后验证**：把主控壳 cid 填入 `scripts/verify_release.py` 的 `MODMATH_CID`，
   运行 `python scripts/verify_release.py --modmath-only --chain-only` →
   确认字节指纹一致、sha256=4767118e…、REF 链正确
6. **提现闭环**：新处理器 Circuits 合约 withdraw() 收回 mint 款

### 3.4 链上行为验证（流片后，可选）

经见证合约 beat() 复放本地对拍序列的关键片段（mod_add 5+7=12 约 20 拍、
环写读各 4 拍），与本地 SimWithREF 逐拍比对。整次 mod_mul 的 beat 成本过高
（mcore 内部逐拍全量求值），按 ECREC 惯例：链上只做关键 I/O 见证，
全量对拍留本地（证据 = 字节回读 + 本地全绿）。

---

## 四、ECREC 兼容性

- ModMath 的 4 条 REF 复用 ECREC v2 零件 → 零件层字节级同源
- ECREC 主控（cid8）与 ModMath 壳可互相 REF 对方电路（REF 无需许可、零消耗）
- ModMath 壳引脚 74→72 ≤255 → 本身即可作为一颗零件被第三方 REF
- 网格纪律（ph0 对齐、窗口 ≡0 mod 4）与 piso256/ringbus/mcore256 完全一致
