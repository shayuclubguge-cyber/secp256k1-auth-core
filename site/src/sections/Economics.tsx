import { BSCSCAN_TOKENS, TAPEOUT_URL } from "../lib/site";

export default function Economics() {
  return (
    <section className="bg-zinc-950 py-20">
      <div className="mx-auto max-w-4xl px-6">
        <h2 className="text-center text-3xl font-bold text-zinc-50">
          拥有这台处理器的一部分
        </h2>
        <p className="mx-auto mt-3 max-w-2xl text-center text-sm leading-relaxed text-zinc-500">
          TapeOut 协议里，每台处理器的与非门（NAND）和锁存器（LATCH）都是
          ERC-1155 代币。铸造新电路要消耗它们——你的电路每 REF
          引用一次这台处理器的零件，都是对它的一次使用。
        </p>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
            <p className="font-mono text-2xl font-bold text-amber-400">NAND</p>
            <p className="mt-2 text-sm text-zinc-400">
              与非门代币（id 0）。铸造任何电路的基本砖块，本处理器由 31,890
              个与非门构成。
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
            <p className="font-mono text-2xl font-bold text-amber-400">LATCH</p>
            <p className="mt-2 text-sm text-zinc-400">
              锁存器代币（id 1）。有状态电路的记忆单元，本处理器含 8,606
              个锁存器。
            </p>
          </div>
          <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-6">
            <p className="font-mono text-2xl font-bold text-zinc-50">
              0.0001 BNB
            </p>
            <p className="mt-2 text-sm text-zinc-400">
              每枚代币铸造价。在 TapeOut
              官网连接钱包、选中本处理器即可铸造，随后可铸造自己的电路。
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
