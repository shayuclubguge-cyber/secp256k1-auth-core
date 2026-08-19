// 站点数据 —— 全部取自链上实测（2026-08-18）
export const SITE_NAME_ZH = "BSC签名验证核心";
export const SITE_NAME_EN = "BSC SignVerify Core";
export const TAGLINE =
  "第一台用纯与非门在链上重建 ecrecover 的处理器";

export const CPU_ADDRESS = "0xa3b6d9121146c29fb236001b93a45cb7a78a2247";
export const TRANSISTORS_ADDRESS =
  "0x40666990f6740a41e51391cec6559a2158177bf2";
export const TAPEOUT_URL = "https://tapeout.oldorange.club/";
export const BSCSCAN_CPU = `https://bscscan.com/address/${CPU_ADDRESS}`;
export const BSCSCAN_TOKENS = `https://bscscan.com/address/${TRANSISTORS_ADDRESS}`;

export const STATS = [
  { value: "13,602", label: "与非门（NAND）" },
  { value: "3,947", label: "锁存器（LATCH）" },
  { value: "10", label: "链上电路（cid 1–13）" },
  { value: "100%", label: "字节级回读一致" },
];

export interface CircuitRow {
  cid: string;
  name: string;
  desc: string;
  pins: string;
  bytes: string;
  sha?: string;
}

export const CIRCUITS: CircuitRow[] = [
  { cid: "1", name: "fadd64", desc: "64 位字串行加法器 ALU", pins: "67→65", bytes: "21,447", sha: "c22f8c74b0de" },
  { cid: "2–6", name: "基础零件 ×5", desc: "mcore256 / piso256 / ringbus5 等", pins: "—", bytes: "21k / 7.8k / 18.7k…", sha: "" },
  { cid: "7", name: "keccak_comp", desc: "Keccak 三模式组件（θD / XOR3 / χ）", pins: "204→64", bytes: "14,854", sha: "2e0a1609fb8f" },
  { cid: "8", name: "ecrecover_ctrl", desc: "secp256k1 宏指令序列器主控", pins: "—", bytes: "24,351", sha: "9cad2f38db0e" },
  { cid: "9", name: "kl_ringa_lo", desc: "Keccak 状态环 A · 低 32 位片（800 锁存器）", pins: "38→64", bytes: "23,682", sha: "5f62ced6d705" },
  { cid: "10", name: "kl_ringa_hi", desc: "Keccak 状态环 A · 高 32 位片（同构双实例）", pins: "38→64", bytes: "23,682", sha: "5f62ced6d705" },
  { cid: "11", name: "kl_rho", desc: "ρ 筒式移位器（6 级 barrel，纯组合）", pins: "69→64", bytes: "12,740", sha: "a5dd3b52cb1b" },
  { cid: "12", name: "kl_ringb", desc: "状态环 B（1,600 锁存器）+ χ 操作数装配", pins: "75→192", bytes: "26,261", sha: "c2be8eefec19" },
  { cid: "13", name: "kl_top", desc: "主控状态机：7 条 REF 缝合 cid 7/9/10/11/12", pins: "65→66", bytes: "27,350", sha: "dccbd34519c3" },
];

export const REF_EDGES = [
  { from: "kl_top (cid13)", to: "keccak_comp (cid7)", label: "θD / XOR3 / χ ×3" },
  { from: "kl_top (cid13)", to: "kl_ringa_lo (cid9)", label: "环 A 低 32 位" },
  { from: "kl_top (cid13)", to: "kl_ringa_hi (cid10)", label: "环 A 高 32 位" },
  { from: "kl_top (cid13)", to: "kl_rho (cid11)", label: "ρ 移位" },
  { from: "kl_top (cid13)", to: "kl_ringb (cid12)", label: "环 B + χ 装配" },
];

// 演示签名（Hardhat 公开测试账户 #0，私钥全网公开，仅作演示）
export const EXAMPLE = {
  message: "Hello, BSC SignVerify Core!",
  address: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
  signature:
    "0xe18bb9e81709122f46c03436c26d26f6dc303ff0be59e21c6bd5d935c1aaafab3a4b85e8187dc5c7bb710ff4882ba5d626c30b3b5f8ef10ee2c6144e56ae49221b",
};
