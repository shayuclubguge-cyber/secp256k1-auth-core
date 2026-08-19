import { useState } from "react";
import { hashMessage, verifyMessage, getAddress } from "ethers";
import { EXAMPLE } from "../lib/site";

interface Result {
  digest: string;
  recovered: string;
  expected: string;
  match: boolean;
}

export default function Verifier() {
  const [message, setMessage] = useState("");
  const [signature, setSignature] = useState("");
  const [address, setAddress] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");

  function fillExample() {
    setMessage(EXAMPLE.message);
    setSignature(EXAMPLE.signature);
    setAddress(EXAMPLE.address);
    setResult(null);
    setError("");
  }

  function run() {
    setError("");
    setResult(null);
    try {
      if (!message) throw new Error("请输入消息内容");
      if (!/^0x[0-9a-fA-F]{130}$/.test(signature.trim()))
        throw new Error("签名格式不对：应为 0x 开头的 65 字节十六进制（132 字符）");
      const expected = getAddress(address.trim()); // 校验地址格式（含 EIP-55）
      const digest = hashMessage(message);
      const recovered = getAddress(verifyMessage(message, signature.trim()));
      setResult({
        digest,
        recovered,
        expected,
        match: recovered.toLowerCase() === expected.toLowerCase(),
      });
    } catch (e: any) {
      setError(e?.shortMessage || e?.message || String(e));
    }
  }

  const inputCls =
    "w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-3 font-mono text-sm text-zinc-100 placeholder-zinc-600 outline-none transition focus:border-amber-500";

  return (
    <section id="verify" className="bg-zinc-950 py-20">
      <div className="mx-auto max-w-3xl px-6">
        <h2 className="text-center text-3xl font-bold text-zinc-50">
          现场验证一条签名
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-center text-sm leading-relaxed text-zinc-500">
          这里用软件库瞬时演示同一套算法；链上处理器以 13,602
          个与非门逐门执行完全相同的运算——Keccak-256 摘要 → secp256k1
          公钥恢复 → 地址比对，每一步的电路网表都可在下方名册中查验。
        </p>

        <div className="mt-10 space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              消息（EIP-191 个人签名格式）
            </label>
            <textarea
              className={inputCls + " h-20 resize-none"}
              placeholder="例如：Hello, BSC SignVerify Core!"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              签名（65 字节 r‖s‖v）
            </label>
            <input
              className={inputCls}
              placeholder="0x…（130 个十六进制字符）"
              value={signature}
              onChange={(e) => setSignature(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              声称的签名地址
            </label>
            <input
              className={inputCls}
              placeholder="0x…（42 个字符）"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={run}
              className="flex-1 rounded-lg bg-amber-500 py-3 font-bold text-zinc-950 transition hover:bg-amber-400"
            >
              验证
            </button>
            <button
              onClick={fillExample}
              className="rounded-lg border border-zinc-700 px-5 text-sm text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
            >
              填入示例
            </button>
          </div>
          {error && (
            <p className="rounded-lg border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-300">
              {error}
            </p>
          )}
        </div>

        {result && (
          <div className="mt-6 space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6 font-mono text-xs">
            <Step n={1} title="Keccak-256 消息摘要（链上 cid 9–13 执行的运算）">
              {result.digest}
            </Step>
            <Step n={2} title="secp256k1 公钥恢复 → 恢复出的地址">
              <span className={result.match ? "text-emerald-400" : "text-red-400"}>
                {result.recovered}
              </span>
            </Step>
            <Step n={3} title="与声称地址比对">
              {result.expected}
            </Step>
            <div
              className={
                "mt-4 rounded-lg px-4 py-4 text-center text-base font-bold " +
                (result.match
                  ? "bg-emerald-500/15 text-emerald-400"
                  : "bg-red-500/15 text-red-400")
              }
            >
              {result.match
                ? "✓ 裁决：签名有效 —— 这条消息确实是该地址签的"
                : "✗ 裁决：签名与地址不符"}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/20 font-bold text-amber-400">
        {n}
      </span>
      <div className="min-w-0">
        <p className="mb-1 font-sans text-zinc-400">{title}</p>
        <p className="break-all text-zinc-200">{children}</p>
      </div>
    </div>
  );
}
