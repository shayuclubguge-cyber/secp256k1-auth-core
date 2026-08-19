# 社区认证申请：ModMath Core

**申请人**：secp256k1 Auth Core（ECREC）作者
**申请日期**：2026-08-19

---

## 项目名称

**ModMath Core**（symbol: MMAT）

## 定位

**TapeOut 生态第一个通用密码学运算库**（General Cryptographic Arithmetic Library）——
一颗独立的密码学数学基础设施处理器，把有限域模加、模乘、位流序列化、环总线传输
四件密码学基础运算封装成统一接口的可引用电路。

## 功能

| 标准接口 | 语义 | 底层电路 |
|---|---|---|
| `mod_add(a, b, m)` / `mod_sub(a, b, m)` | 任意 256 位模数加减 | fadd64b（字串行 ALU） |
| `mod_mul(a, b, m)` | 任意模数 m > 2^255 的 256 位模乘 | mcore256（自包含模乘引擎） |
| `serialize(data)` | 256 位并转串（MSB 先行位流） | piso256 |
| `bus_transfer(src, dst)` | 5×256 位环寄存器堆数据流转 | ringbusA |

一颗电路服务任意素数域（模数是运行时装入的参数，不是烧死的常数）——
secp256k1、secp256r1、brainpoolP256r1 及任何 m > 2^255 的素数域即插即用。

## 证据

0. **已上线可查证**：ModMath Core（MMAT）处理器已在 BSC 主网运行 ——
   Circuits `0x9b30f16fb5f0c91343e678d94670a0d8b231a40d`（官网
   `https://tapeout.net/#p/0x9b30f16fb5f0c91343e678d94670a0d8b231a40d`），
   主控壳为其 cid1，创建 tx `0xfdb16d99…`，流片 tx `0xf9bdd26d…`。
1. **基于已链上验证资产**：主控壳 4 条 REF 全部指向 ECREC v2 处理器
   （`0xa3b6d9121146c29fb236001b93a45cb7a78a2247`）已上链零件
   cid 6 / 2 / 3 / 4，链上字节回读与本地逐字节一致：
   - fadd64b：sha256 `c22f8c74b0def33c…`
   - mcore256：sha256 `37eb121673dbf830…`
   - piso256：sha256 `356cdf33ca964192…`
   - ringbusA：sha256 `83beeeb0b50e4bae…`
2. **本地对拍全绿**：mod-p 模乘 14/14、mod-n 模乘 5/5、通用素数域 5/5
   （secp256r1 p/n、brainpoolP256r1 p、2^256−189、2^255+95，均过 Miller-Rabin）、
   并转串 3/3、总线传输 2/2，另加 mod_add/mod_sub 16 组与 REF 状态隔离验证——
   合计 45 组向量 100% 与黄金模型一致。
3. **引脚全部 ≤255**（74 入 / 72 出），符合生态易引用标准，任何处理器可直接 REF。
4. **字节指纹**：主控壳网表 6,337B，
   sha256 `4767118eb724134a6964249b0327630e7eb609586cc1299bcc86e3773647b13b`，
   源码现场重建与存档逐字节一致。
5. **一键验证脚本开源**：`scripts/verify_release.py --modmath-only`
   （本地全量对拍 + BSC 主网链上回读比对，退出码 0/1）。
6. **资源透明**：主控壳 771 NAND + 0 LATCH + 4 REF；
   逻辑总量（壳 + 4 零件）11,767 晶体管；预估流片 gas ~2.7M ≪ BEP-652 上限。

## 差异化

| 项目 | 定位 |
|---|---|
| 老橘子 | 哈希整机（KECCAK / SHA256 库） |
| Blonskr | 通用 CPU |
| **ModMath Core** | **密码学数学砖块——有限域运算基础设施（全生态空白）** |

哈希需要数学，签名需要数学，任何密码学处理器都需要 mod_mul/mod_add。
ModMath Core 不做整机，只做所有整机脚下那块经过验证的数学地基。

## 兼容性承诺

ModMath Core 与 ECREC 零件层字节级同源、可互相 REF；REF 零消耗、无需许可。
主控壳开源，驱动协议（拍级）随库发布，任何人可在自己的处理器上引用、
验证、复放全部测试向量。
