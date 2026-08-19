# Keccak Core —— 零件复用验证报告

- 生成时间: 2026-08-19 23:26 UTC+08:00（脚本 `scripts/verify_release.py`，耗时 11s）
- 处理器合约: `0xa3b6d9121146c29fb236001b93a45cb7a78a2247`（BNB Chain 主网）
- 本地测试: 本次跳过（--chain-only）
- 链上回读（Keccak 零件 cid 7, 9–13）: 6/6 颗字节级一致
- 合计: 128,569 字节网表，15,724 NAND + 3,947 LATCH = 19,671 晶体管

| cid | 电路 | 引脚 | 字节 | NAND | LATCH | REF | sha256[0:12] | 一致 |
|---|---|---|---|---|---|---|---|---|
| 7 | keccak_comp θD/XOR3/χ | 204→64 | 14,854 | 2,122 | 0 | 0 | 2e0a1609fb8f | ✓ |
| 9 | kl_ringa_lo 环A低32位 | 38→64 | 23,682 | 2,926 | 800 | 0 | 5f62ced6d705 | ✓ |
| 10 | kl_ringa_hi 环A高32位 | 38→64 | 23,682 | 2,926 | 800 | 0 | 5f62ced6d705 | ✓ |
| 11 | kl_rho ρ移位 | 69→64 | 12,740 | 1,820 | 0 | 0 | a5dd3b52cb1b | ✓ |
| 12 | kl_ringb 环B+χ装配 | 75→192 | 26,261 | 2,691 | 1,856 | 0 | c2be8eefec19 | ✓ |
| 13 | kl_top 主控 REF×7 | 65→66 | 27,350 | 3,239 | 491 | 7 | dccbd34519c3 | ✓ |

> 比对方式: eth_call `netlist(cid)` 回读链上字节 → SHA-256 →
> 与 CHAIN_ASSETS.md 定稿的链上指纹前缀交叉比对（复用以链上字节为准）。
> cid13 kl_top 的 REF×7 目标已解析校验：cid7×3 + cid9/10/11/12，均属本处理器。
> 指纹基准与 `vectors/kl_split_manifest.json`（2026-08-19 已同步）一致。
