# keccak_link 设计定稿（2026-08-18，第三轮分析）

## 结论先行

**可造，但不是 8-10KB，也不是 OKX 可发。** 真实预算 ~10,400 指令 / ~68KB：
- 超过 OKX 钱包 24,575B 上限 → **流片交易走 MetaMask**（68KB calldata ≈ 1.1M gas，没问题）
- 每拍 beat gas ≈ 10,400 × 2,318 ≈ 24M < MetaMask 55M ✓ 可见证

之前"8-10KB"的估算错误：它漏掉了 π（车道置换）。π 的任何流式实现
都需要 25 选 1 的 64 位 mux（~5,000 NAND ≈ 35KB 等效）或等量交叉开关，
这是 Keccak 流式化的固有成本，已排除所有绕行方案（剪切分解不存在、
环旋转时序只能实现循环移位、双缓冲/寄存器堆都要同样的 mux）。

## 架构：单电路，四阶段轮转 × 24 轮

状态存储：
- **state 环**：1,600 位 rot64 自由环（25 车道），底片=当前车道，固定 tap
  slice0..4 可读（s1=buf[64:128] 等）
- **CD 环**：320 位（5 车道 rot64），θ 列奇偶 C[x] / χ 行缓冲复用
- **buf0/buf1**：2×64 位，χ 的行内回绕车道缓存（x=3,4 需要已消费车道）

共享运算部件（全部时分复用）：
- **REF×2 → keccak 组件**（v1 cid4 同网表，需在 v2 补流片；204 针：
  3×64 lane + lane_id5 + round5 + mode2；mode 00=χ 01=XOR3）
  - REF1 专用：D[x] = XOR3(C[x−1], rot1(C[x+1]), 0)，a/b/c 全固定 tap 零 mux
  - REF2 通用：θ1 累加 / θ3 施加 / χ / 吸收，全用一个
- **筒式移位器**：64 位 × 6 级（~1,152 NAND），只在 θ3 施加 ρ 旋转
- **π mux**：25 选 1 × 64 位（~5,000 NAND）+ 拍号译码 ROM（~200）

## 拍级流程（总 ~3,077 拍）

```
ABSORB(25 拍)：tick 0..16：REF2(XOR3, bottom, in_word, 0) 顶装 state
  （驱动器喂 8 车道数据 + 0x01 填充 + 0x80… 末字节常数，全公开可复放）
  tick 17..24：we=0 纯旋转。之后 24 轮：

每轮 r：
  P1 θ 累加（25 拍）：CD 顶装 = tick<5 ? state_bottom（b=c=0 直通）
                      : REF2(XOR3, state_bottom, CD_s4, 0)
    ※ C[x] 在 tick x 写入顶片，之后每次用到都在 slice4（(4−5k)≡4 mod 5），
      故读 tap = CD_s4 固定
    ※ state 环 we=0 纯旋转，25 拍后复原
  P2 θ 施加+ρ（25 拍）：tick τ（车道 τ 在底片，C 环对齐：C[x] 在 CD_s4，
    C[x±1] 在 CD_s3/s0——按 P1 结束相位推算，实现时以仿真对拍为准）
    D = REF1(XOR3, CD_s3, rot1(CD_s0), 0)
    out = REF2(XOR3, state_bottom, D, 0)
    state 顶装 = barrel(out, ρ[τ])（ρ 表为 tick→6bit 固定 ROM 布线）
    we=1 每拍；写入时刻=读取时刻 → 槽位恒等保持（tick τ 写入→最终槽 τ）
  P3 χ+ι（27 拍）：buf0←bottom@tick≡0(mod5)，buf1←bottom@tick≡1
    x=0,1,2: REF2(χ, a=s0, b=s1, c=s2)
    x=3:     REF2(χ, a=s0, b=s1, c=buf0)
    x=4:     REF2(χ, a=s0, b=buf0, c=buf1)
    ι：REF1_c 平时为 0，P3 tick0 时改为 RC 引脚（64 AND 门控，
      驱动器在该拍喂 RC[r]）——χ_out ^= RC 经 CD 顶装路径完成
      （或 REF2 输出后再 XOR，取实现便宜者）
    CD 顶装 = χ_out（tick τ 产出车道 τ）
    state 顶装 = CD_s2（=车道 τ−2 的 χ 输出），we = tick≥2
    27 拍写入映射：tick t 写入→最终槽 (24+t−26)≡t−2 ✓ 车道 L 在 tick L+2
    写入槽 L ✓ 双拍延迟被 CD 环 slice2 固定 tap 吸收
  P4 π（50 拍）：tick 0..24：we=0 纯旋转（车道保全）
    tick 25..49：state 顶装 = πmux(tap=(π⁻¹(w)−w mod 25) 片, w=tick−25)
    写入 tick 25+w → 最终槽 w ✓；π 完成，车道顺序复原为新状态
```

轮末槽位对齐：P1(25)≡0、P2(25)≡0、P3(27) 写入自带修正、P4(50)≡0，
每轮结束 state 槽位=逻辑车道号 ✓ 可进下一轮。

## SQUEEZE（4 拍）

out_word = state 底片流出 4 拍（车道 0..3 = 摘要 32 字节），
地址 = 字节 [12:32]（驱动器/演示站截取）。完成后 done=1。

## 引脚（≤255 ✓）

入 66+：in_word64 / start / （可选 cmd） 
出 66：out_word64 / busy / done

## FSM

tick7（0..76 内轮 tick：P1 0-24 / P2 25-49 / P3 50-76 / P4 77-126 ——
改为轮内统一 127 拍计数或分段计数器，实现时选便宜者）+ round5 + x3(tick mod 5)。
~300 NAND。

## 门数/字节预算（实测后回填）

| 部件 | 指令 | 字节 |
|---|---|---|
| state 环 1600 L | 1,600 | 6,400 |
| CD 环 320 L + buf 128 L | 448 | 1,792 |
| π mux 25 选 1 | ~5,000 | ~35,000 |
| 筒式移位器 6 级 | ~1,152 | ~8,064 |
| REF2 端口 mux（b 4 路 + c 4 路 + 写数据 4 路） | ~1,700 | ~11,900 |
| FSM/计数器/译码 | ~300 | ~2,100 |
| REF×2 | 2 | ~1,300 |
| **合计** | **~10,500** | **~66,500** |

beat gas ≈ 24M（MetaMask 可）；流片 tx ≈ 66KB ≈ 1.1M gas（MetaMask 可）。

## 验证计划

1. 结构：指令数 ≤23,700（beat≤55M）、引脚 ≤255
2. 逐阶段对拍：黄金 keccak_f（tests/test_keccak.py 的 keccak_f_round_ref
   逐轮中间值 + pycryptodome 终值）
3. 端到端：keccak256(b"") 与 keccak256(Qx‖Qy)（ecrecover 向量）
4. 与 ctrl 的 Qx/Qy READ_RING 输出对接（驱动器层拼接）

## 信任模型说明

keccak_link 是独立电路（独立 cid），输入 Qx‖Qy 由 ecrecover_ctrl 的
READ_RING 见证锁定，输出地址同样逐拍可见证。全链路 =
签名 →(ctrl, 已上链)→ Qx‖Qy →(keccak_link)→ 地址，每跳都有网表指纹。
