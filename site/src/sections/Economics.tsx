import { BSCSCAN_TOKENS, TAPEOUT_URL } from "../lib/site";

export default function Economics() {
  return (
    <section className="bg-zinc-950 py-20">
      <div className="mx-auto max-w-4xl px-6">
        <h2 className="text-center text-3xl font-bold text-zinc-50">
          这台机器的零件也是代币
        </h2>
        <p className="mx-auto mt-3 max-w-2xl text-center text-sm leading-relaxed text-zinc-500">
          TapeOut 里，NAND 和 LATCH 都是 ERC-1155 代币。造新电路要消耗它们。
          你的电路每引用一次我们的零件，都是在用这台机器。
        </p>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
            <p className="font-mono text-2xl font-bold text-amber-400">NAND</p>
            <p className="mt-2 text-sm text-zinc-400">
              代币（id 0）。造电路的基本砖块。这台机器用了 31,890 个。
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
            <p className="font-mono text-2xl font-bold text-amber-400">LATCH</p>
            <p className="mt-2 text-sm text-zinc-400">
              代币（id 1）。有状态电路的记忆单元。这台机器用了 8,606 个。
            </p>
          </div>
          <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-6">
            <p className="font-mono text-2xl font-bold text-zinc-50">
              0.0001 BNB
            </p>
            <p className="mt-2 text-sm text-zinc-400">
              一枚。去 TapeOut 官网连钱包，选这台处理器就能铸造。
              然后你可以造自己的电路。
            </p>
          </div>
        </div>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <a
            href={TAPEOUT_URL}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg bg-amber-500 px-8 py-3 font-bold text-zinc-950 transition hover:bg-amber-400"
          >
            前往铸造 NAND / LATCH
          </a>
          <a
            href={BSCSCAN_TOKENS}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-zinc-700 px-8 py-3 text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
          >
            查看代币合约
          </a>
        </div>
      </div>
    </section>
  );
}
