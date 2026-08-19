# ModMath Core —— 一键验证报告

- 生成时间: 2026-08-19 22:43 UTC+08:00（脚本 `scripts/verify_release.py`，耗时 11s）
- 处理器合约: `0xa3b6d9121146c29fb236001b93a45cb7a78a2247`（BNB Chain 主网）
- 本地测试: 本次跳过（--chain-only）
- 链上回读（ModMath 依赖零件 cid 1–6 + 主控壳）: 7/7 颗字节级一致
- 合计: 115,200 字节网表，13,792 NAND + 4,371 LATCH = 18,163 晶体管

| cid | 电路 | 引脚 | 字节 | NAND | LATCH | REF | sha256[0:12] | 一致 |
|---|---|---|---|---|---|---|---|---|
| 1 | fadd64 字串行ALU | 67→65 | 21,447 | 2,917 | 257 | 0 | c22f8c74b0de | ✓ |
| 2 | mcore256 自包含模乘引擎 | — | 20,769 | 2,339 | 1,041 | 1 | 37eb121673db | ✓ |
| 3 | piso256 并转串 | — | 7,772 | 964 | 256 | 0 | 356cdf33ca96 | ✓ |
| 4 | ringbusA 环总线 | — | 18,714 | 1,942 | 1,280 | 0 | 83beeeb0b50e | ✓ |
| 5 | ringbusB（同网表二实例） | — | 18,714 | 1,942 | 1,280 | 0 | 83beeeb0b50e | ✓ |
| 6 | fadd64b（同网表二实例） | 67→65 | 21,447 | 2,917 | 257 | 0 | c22f8c74b0de | ✓ |
| 1 | MMAT modmath_ctrl 主控壳 REF×4 | 74→72 | 6,337 | 771 | 0 | 4 | 4767118eb724 | ✓ |

> 比对方式: eth_call `netlist(cid)` 回读链上字节 → SHA-256 →
> 与本地 `vectors/*.net`（cid2 由 `tapeout/parts/mcore256.py` 现场重建）逐字节比对。

## ModMath 主控壳（modmath_ctrl）

- 引脚: 74→72（≤255 ✓）  资源: 771 NAND + 0 LATCH + 4 REF
- 网表: `vectors/modmath_ctrl.net`（6,337B）
- sha256: `4767118eb724134a6964249b0327630e7eb609586cc1299bcc86e3773647b13b`
- REF 缝合: fadd64b cid6（mod_add）/ mcore256 cid2（mod_mul）/ piso256 cid3（serialize）/ ringbusA cid4（bus_transfer）
- 链上状态: cid1 已回读比对
