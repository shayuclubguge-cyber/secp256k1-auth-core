import { CIRCUITS, REF_EDGES, BSCSCAN_CPU } from "../lib/site";

export default function Architecture() {
  return (
    <section className="border-y border-zinc-800/80 bg-zinc-900/40 py-20">
      <div className="mx-auto max-w-5xl px-6">
        <h2 className="text-center text-3xl font-bold text-zinc-50">
          处理器结构：一条 REF 引用链
        </h2>
        <p className="mx-auto mt-3 max-w-2xl text-center text-sm leading-relaxed text-zinc-500">
          受 BSC 单交易 gas 协议上限（BEP-652，16.7M）约束，Keccak-256
          引擎被拆成 5 颗可独立流片的零件，由主控电路 kl_top 通过 7 条
          REF 指令在每次求值时缝合——这正是 TapeOut
          「电路引用电路」原语的旗舰级示范。
        </p>

        {/* REF 结构图 */}
        <div className="mx-auto mt-12 max-w-2xl">
          <div className="rounded-xl border-2 border-amber-500/60 bg-zinc-950 px-6 py-4 text-center">
            <p className="font-mono text-lg font-bold text-amber-400">
              kl_top <span className="text-xs text-zinc-500">cid13 · 主控状态机</span>
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              65 入 66 出 · 27,350 B · 逐拍驱动 2,400+ 拍完成一次 Keccak-256
            </p>
          </div>
          <div className="mx-auto h-6 w-px bg-amber-500/50" />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {[
              { cid: "cid7", name: "keccak_comp", sub: "θD/XOR3/χ ×3 次" },
              { cid: "cid9", name: "kl_ringa_lo", sub: "环A低32位 800L" },
              { cid: "cid10", name: "kl_ringa_hi", sub: "环A高32位 800L" },
              { cid: "cid11", name: "kl_rho", sub: "ρ 筒式移位" },
              { cid: "cid12", name: "kl_ringb", sub: "环B 1600L+χ装配" },
            ].map((p) => (
              <div
                key={p.cid}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-3 text-center"
              >
                <p className="font-mono text-[11px] text-amber-500/80">{p.cid}</p>
                <p className="font-mono text-sm font-semibold text-zinc-200">
                  {p.name}
                </p>
                <p className="mt-1 text-[11px] text-zinc-500">{p.sub}</p>
              </div>
            ))}
          </div>
          <ul className="mt-6 space-y-1 font-mono text-[11px] text-zinc-600">
            {REF_EDGES.map((e) => (
              <li key={e.to}>
                REF {e.from} → {e.to} <span className="text-zinc-700">({e.label})</span>
              </li>
            ))}
          </ul>
        </div>

        {/* 电路名册 */}
        <h3 className="mt-20 text-center text-2xl font-bold text-zinc-50">
          链上电路名册
        </h3>
        <p className="mt-2 text-center text-sm text-zinc-500">
          每颗电路的网表字节均可从合约回读并与本地指纹逐字节比对
        </p>
        <div className="mt-8 overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900 text-xs text-zinc-500">
                <th className="px-4 py-3 font-medium">cid</th>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">功能</th>
                <th className="px-4 py-3 font-medium">引脚</th>
                <th className="px-4 py-3 font-medium">字节</th>
                <th className="px-4 py-3 font-medium">sha256 前 12 位</th>
              </tr>
            </thead>
            <tbody className="font-mono text-xs">
              {CIRCUITS.map((c) => (
                <tr
                  key={c.cid}
                  className="border-b border-zinc-800/60 text-zinc-300 last:border-0 hover:bg-zinc-900/60"
                >
                  <td className="px-4 py-3 text-amber-400">{c.cid}</td>
                  <td className="px-4 py-3 font-semibold">{c.name}</td>
                  <td className="px-4 py-3 font-sans text-zinc-400">{c.desc}</td>
                  <td className="px-4 py-3">{c.pins}</td>
                  <td className="px-4 py-3">{c.bytes}</td>
                  <td className="px-4 py-3 text-zinc-500">{c.sha || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-center text-xs text-zinc-600">
          处理器合约：
          <a
            className="ml-1 font-mono text-amber-500/80 hover:text-amber-400"
            href={BSCSCAN_CPU}
            target="_blank"
            rel="noreferrer"
          >
            0xa3b6d912…78a2247 ↗
          </a>
        </p>
      </div>
    </section>
  );
}
