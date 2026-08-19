# secp256k1 Auth Core — 纯与非门链上签名验证处理器

**在 BNB Chain 上，用 40,496 个晶体管（31,890 NAND + 8,606 LATCH）徒手重建 ecrecover。**
13 颗电路全部上链，全部字节级可验证。

The first on-chain ecrecover rebuilt from raw NAND gates — 13 circuits, 40,496 transistors, byte-verified on BNB Chain.

- 处理器主页（TapeOut 协议）: https://tapeout.net/#p/0xa3b6d9121146c29fb236001b93a45cb7a78a2247
- 演示站: https://bsc-signverify.github.io/secp256k1-auth-core/
- **新手教程（买晶体管 → 画布搭电路 → REF 我们的零件 → 流片）**: [`docs/TUTORIAL.md`](docs/TUTORIAL.md)
- Circuits 合约: [`0xa3b6d9121146c29fb236001b93a45cb7a78a2247`](https://bscscan.com/address/0xa3b6d9121146c29fb236001b93a45cb7a78a2247)
- Transistors (ERC-1155, NAND=id 0 / LATCH=id 1): [`0x40666990f6740a41e51391cec6559a2158177bf2`](https://bscscan.com/address/0x40666990f6740a41e51391cec6559a2158177bf2)
- 经济参数：总量 1,000,000 封顶 · 铸造单价 0.0001 BNB · 合约无修改经济参数的接口（创建交易回执的工厂事件内嵌全部参数，链上可查）

## 不相信？自己验证（约 20 秒）

```bash
python scripts/verify_release.py --chain-only   # 只用 Python 标准库，无需安装任何依赖
```

逐颗 eth_call `netlist(cid)` 回读 BSC 主网字节流 → SHA-256 → 与本仓库 `vectors/*.net` 逐字节比对（cid2 由 `tapeout/parts/mcore256.py` 现场重建）。预期输出：**13/13 一致**。

想当审计员？跑这个（含 Keccak 组件链上功能实时复现、铸造事件统计、封顶/单价边界模拟）：

```bash
python scripts/independent_audit.py    # 约 3–5 分钟，只用公开 RPC
```

审计报告：[`docs/INDEPENDENT_AUDIT.md`](docs/INDEPENDENT_AUDIT.md) · 验证报告：[`docs/VERIFY_REPORT.md`](docs/VERIFY_REPORT.md)

## 链上电路名册（cid 1–13）

| cid | 电路 | 引脚 | 字节 | NAND | LATCH | sha256[0:12] |
|---|---|---|---|---|---|---|
| 1 | fadd64 字串行 ALU | 67→65 | 21,447 | 2,917 | 257 | c22f8c74b0de |
| 2 | mcore256 模乘引擎 | — | 20,769 | 2,339 | 1,041 | 37eb121673db |
| 3 | piso256 并转串 | — | 7,772 | 964 | 256 | 356cdf33ca96 |
| 4/5 | ringbusA/B（同网表×2） | — | 18,714 | 1,942 | 1,280 | 83beeeb0b50e |
| 6 | fadd64b（同网表） | 67→65 | 21,447 | 2,917 | 257 | c22f8c74b0de |
| 7 | keccak_comp（θD/XOR3/χ 三模式） | 204→64 | 14,854 | 2,122 | 0 | 2e0a1609fb8f |
| 8 | ecrecover_ctrl 宏指令主控 | — | 24,351 | 3,145 | 288 | 9cad2f38db0e |
| 9/10 | kl_ringa_lo/hi（同构×2） | 38→64 | 23,682 | 2,926 | 800 | 5f62ced6d705 |
| 11 | kl_rho ρ 移位 | 69→64 | 12,740 | 1,820 | 0 | a5dd3b52cb1b |
| 12 | kl_ringb 环B + χ 装配 | 75→192 | 26,261 | 2,691 | 1,856 | c2be8eefec19 |
| 13 | kl_top 主控（REF×7 缝合 cid 7/9/10/11/12） | 65→66 | 27,350 | 3,239 | 491 | dccbd34519c3 |

## 工程背景

BSC 单交易 gas 协议上限 16,777,216（BEP-652），实测 gas ≈ 421 × 网表字节 → 单电路 ≤ ~39KB。
110KB 的 Keccak-256 引擎因此被拆成 5 颗可独立流片的零件，由主控 `kl_top` 通过 7 条 REF
指令在求值时缝合——TapeOut「电路引用电路」原语的旗舰级实战。

## 仓库结构

- `tapeout/` — 网表生成器与本地仿真器（NAND / LATCH / REF 三原语）
- `tapeout/parts/` — 各电路零件的构造代码（含 Keccak 黄金模型）
- `vectors/` — 全部链上网表 `.net` 文件 + 清单
- `tests/` — 21 项本地测试（门级对拍 + 黄金模型 + 真实 cid 注册）
- `scripts/verify_release.py` — 一键验证（测试 + 链上回读 + 报告）
- `scripts/independent_audit.py` — 独立审计（自带纯 Python Keccak-256，零项目代码依赖）
- `site/` — 演示站源码（React + Vite + Tailwind + ethers，含现场验签）
- `docs/` — 设计文档、验证报告、审计报告与证据 JSON

## License

MIT
