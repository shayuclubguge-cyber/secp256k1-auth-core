import { Link } from "react-router";
import { STATS, SITE_NAME_ZH, TAGLINE, BSCSCAN_CPU } from "../lib/site";

export default function Hero() {
  return (
    <header className="relative overflow-hidden border-b border-amber-500/20 bg-zinc-950">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(90deg, #f59e0b 0 1px, transparent 1px 48px), repeating-linear-gradient(0deg, #f59e0b 0 1px, transparent 1px 48px)",
        }}
      />
      <div className="relative mx-auto max-w-5xl px-6 py-24 text-center">
        <p className="mb-4 font-mono text-xs tracking-[0.35em] text-amber-500/80 uppercase">
          100% NAND Gates · On-Chain · BNB Chain
        </p>
        <h1 className="text-5xl font-black tracking-tight text-zinc-50 md:text-6xl">
          {SITE_NAME_ZH}
        </h1>
        <p className="mt-3 font-mono text-xl text-amber-400">On-Chain ecrecover Engine</p>
        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-zinc-400">
          {TAGLINE}。用 <span className="text-amber-300">40,496 个晶体管</span>，在
          BNB Chain 上复现了 ecrecover 签名验证。任何合约都能免费调用，回答一个问题：
          <span className="text-zinc-200">「这条消息，是不是这个地址签的？」</span>
        </p>
        <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
          <a
            href="#verify"
            className="rounded-lg bg-amber-500 px-8 py-3 text-base font-bold text-zinc-950 transition hover:bg-amber-400"
          >
            立即验证
          </a>
          <a
            href={BSCSCAN_CPU}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-zinc-700 px-8 py-3 text-base font-medium text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
          >
            链上查看
          </a>
          <Link
            to="/tutorial"
            className="rounded-lg border border-zinc-700 px-8 py-3 text-base font-medium text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
          >
            新手指南：买晶体管 → 搭电路 → 流片
          </Link>
        </div>
        <dl className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-6 md:grid-cols-4">
          {STATS.map((s) => (
            <div
              key={s.label}
              className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-5"
            >
              <dt className="order-2 mt-1 text-xs text-zinc-500">{s.label}</dt>
              <dd className="font-mono text-2xl font-bold text-amber-400">
                {s.value}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </header>
  );
}
