import { SITE_NAME_EN, SITE_NAME_ZH, TAPEOUT_URL } from "../lib/site";

export default function Footer() {
  return (
    <footer className="border-t border-zinc-800 bg-zinc-950 py-10">
      <div className="mx-auto flex max-w-5xl flex-col items-center gap-3 px-6 text-center">
        <p className="font-mono text-sm text-zinc-400">
          {SITE_NAME_ZH} · <span className="text-amber-500/80">{SITE_NAME_EN}</span>
        </p>
        <p className="max-w-xl text-xs leading-relaxed text-zinc-600">
          哈希、算术、资产、授权——链上重建的最后一块基石。
          基于{" "}
          <a
            href={TAPEOUT_URL}
            target="_blank"
            rel="noreferrer"
            className="text-zinc-500 underline hover:text-amber-400"
          >
            TapeOut
          </a>{" "}
          协议构建于 BNB Chain。所有电路网表指纹均可独立验证。
        </p>
        <p className="font-mono text-[11px] text-zinc-700">
          NAND × 13,602 · LATCH × 3,947 · cid 1–13 · 2026-08-18 流片完成
        </p>
      </div>
    </footer>
  );
}
