import { Link } from "react-router";
import { TAPEOUT_URL, CPU_ADDRESS } from "../lib/site";

const DETAIL_URL = `https://tapeout.net/#p/${CPU_ADDRESS}`;
const GITHUB_TUTORIAL =
  "https://github.com/shayuclubguge-cyber/secp256k1-auth-core/blob/main/docs/TUTORIAL.md";

function Section({
  id,
  title,
  children,
}: {
  id?: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="mx-auto max-w-3xl px-6 py-10">
      <h2 className="text-2xl font-bold text-zinc-50">{title}</h2>
      <div className="mt-4 space-y-3 text-sm leading-relaxed text-zinc-400">
        {children}
      </div>
    </section>
  );
}

export default function Tutorial() {
  return (
    <main className="min-h-screen bg-zinc-950 font-sans text-zinc-100 antialiased">
      <header className="border-b border-amber-500/20 bg-zinc-950">
        <div className="mx-auto max-w-3xl px-6 py-16 text-center">
          <p className="font-mono text-xs tracking-[0.35em] text-amber-500/80 uppercase">
            Tutorial
          </p>
          <h1 className="mt-3 text-4xl font-black text-zinc-50">
            新手教程：从买晶体管到流片
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-zinc-400">
            完整跟练一遍约 0.001–0.002 BNB（不到 1 美元）。
            画布试玩全程免费，只有按下 TAPE OUT 那一刻才真正烧代币。
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <a
              href={GITHUB_TUTORIAL}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-zinc-700 px-5 py-2 text-sm text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
            >
              GitHub 完整版（含代码）
            </a>
            <Link
              to="/"
              className="rounded-lg border border-zinc-700 px-5 py-2 text-sm text-zinc-300 transition hover:border-amber-500/60 hover:text-amber-300"
            >
              ← 返回首页
            </Link>
          </div>
        </div>
      </header>

      <Section title="第 0 步：30 秒理解这个游戏">
        <p>
          TapeOut 把「造芯片」搬上区块链：<span className="text-zinc-200">晶体管是代币</span>
          （NAND 与非门 + LATCH 锁存器），你在画布上把它们连成电路，按 TAPE OUT
          流片后代币真正烧掉，电路永久刻在链上。
        </p>
        <p>
          最重要的是 <span className="text-amber-300">REF（引用）免费</span>：
          任何人的成品电路都能当黑盒拖进你的画布——不烧代币、双方都不收费，
          你只为自己的原创门付代币。
        </p>
      </Section>

      <Section title="第 1 步：准备工作">
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-zinc-900 text-zinc-300">
              <tr>
                <th className="px-4 py-2">操作</th>
                <th className="px-4 py-2">预期花费</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800 text-zinc-400">
              <tr><td className="px-4 py-2">铸造晶体管</td><td className="px-4 py-2 font-mono">0.0001 BNB/个 + 每笔 0.0001 协议费</td></tr>
              <tr><td className="px-4 py-2">流片一颗小电路</td><td className="px-4 py-2 font-mono">gas 约 0.0008–0.0015 BNB</td></tr>
              <tr><td className="px-4 py-2">REF 引用零件</td><td className="px-4 py-2 font-mono text-amber-400">0（免费）</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          ⚠️ <span className="text-zinc-200">MetaMask 必做设置</span>：默认 RPC 会吞交易
          （显示成功但从未广播）。小狐狸 → 设置 → 网络 → BNB Chain → RPC 改为{" "}
          <code className="rounded bg-zinc-900 px-1 font-mono text-amber-300/90">
            https://bsc-dataseed.binance.org
          </code>
        </p>
      </Section>

      <Section title="第 2 步：买第一批晶体管">
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            打开{" "}
            <a href={DETAIL_URL} target="_blank" rel="noreferrer"
               className="text-amber-400 underline hover:text-amber-300">
              处理器详情页
            </a>{" "}（secp256k1 Auth Core），连接钱包，确认是 BNB Chain
          </li>
          <li>
            选数量铸造。以 100 个 NAND 为例，预期弹窗金额 ={" "}
            <span className="font-mono text-zinc-200">100 × 0.0001 + 0.0001 = 0.0101 BNB</span> + 少量 gas
          </li>
          <li>确认后你就持有了 NAND（ERC-1155 id 0）</li>
        </ol>
        <p className="text-xs text-zinc-500">
          本处理器总量 1,000,000 封顶、单价 0.0001 BNB，创建时写入合约，无修改入口
          （创建交易回执中的工厂事件可独立查证）。
        </p>
      </Section>

      <Section title="第 3 步：画布搭电路（免费试玩）">
        <p>
          打开{" "}
          <a href={TAPEOUT_URL} target="_blank" rel="noreferrer"
             className="text-amber-400 underline hover:text-amber-300">TapeOut 画布</a>，
          拖入晶体管、拉住引脚拖线。画布全程在你浏览器里运行，随便试，不花一分钱。
        </p>
        <p>
          官方练手路径：5 个晶体管 → 半加器；9 个 → 全加器；36 个 → 4 位加法器。
        </p>
      </Section>

      <Section title="第 4 步：REF 引用我们的零件（重点）">
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-zinc-900 text-zinc-300">
              <tr>
                <th className="px-4 py-2">cid</th>
                <th className="px-4 py-2">零件</th>
                <th className="px-4 py-2">引脚</th>
                <th className="px-4 py-2">适合</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800 text-zinc-400">
              <tr><td className="px-4 py-2 font-mono text-amber-400">7</td><td className="px-4 py-2">keccak_comp（θD/XOR3/χ）</td><td className="px-4 py-2 font-mono">204→64</td><td className="px-4 py-2">新手首选，纯组合</td></tr>
              <tr><td className="px-4 py-2 font-mono text-amber-400">1/6</td><td className="px-4 py-2">fadd64 字串行 ALU</td><td className="px-4 py-2 font-mono">67→65</td><td className="px-4 py-2">进阶，4 拍一个运算</td></tr>
              <tr><td className="px-4 py-2 font-mono text-amber-400">2</td><td className="px-4 py-2">mcore256 模乘引擎</td><td className="px-4 py-2 font-mono">—</td><td className="px-4 py-2">高级，内嵌 REF</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          <span className="text-zinc-200">新手例：三路异或器。</span>
          把 cid7 拖进画布：引脚 0–63 接 a、64–127 接 b、128–191 接 c，
          lane/round（192–201）接常量 0，模式脚 202 接 1、203 接 0，
          输出就是 <code className="rounded bg-zinc-900 px-1 font-mono text-amber-300/90">a ⊕ b ⊕ c</code>。
          模式脚改成 00 则得到 χ 非线性层{" "}
          <code className="rounded bg-zinc-900 px-1 font-mono text-amber-300/90">a ⊕ (¬b ∧ c)</code>。
          两种模式均已通过链上实时复现验证。
        </p>
        <p className="text-xs text-zinc-500">
          进阶（fadd64 的 67 引脚全定义、256 位加法驱动流程、代码生成网表示例）
          见 GitHub 完整版教程。
        </p>
      </Section>

      <Section title="第 5 步：流片（TAPE OUT）">
        <ol className="list-decimal space-y-2 pl-5">
          <li>画布上确认行为无误（本地运行免费）</li>
          <li>按 <span className="font-bold text-zinc-200">TAPE OUT</span> → 钱包弹窗 → 确认</li>
          <li>代币烧掉，电路获得永久 cid，任何人都能 REF 它</li>
        </ol>
        <p>
          硬限制（BEP-652）：单交易 gas 上限 16,777,216，实测 gas ≈ 421 × 网表字节，
          即<span className="text-zinc-200">单电路 ≤ 约 39 KB</span>。超过就拆成多颗零件用
          REF 缝合——我们的 Keccak 引擎就是从 110 KB 拆成 5 颗的。
        </p>
      </Section>

      <Section title="常见问题">
        <ul className="space-y-3">
          <li><span className="text-zinc-200">REF 要付钱给零件作者吗？</span><br />不要。零消耗零费用，已实测确认。</li>
          <li><span className="text-zinc-200">同一设计能流片两次吗？</span><br />能。同网表重复流片得到独立实例（cid 1/6、4/5、9/10 就是同构多实例）。</li>
          <li><span className="text-zinc-200">晶体管买错能退吗？</span><br />不能。烧成电路就永久销毁，先小量试。</li>
          <li><span className="text-zinc-200">怎么确认这些零件没吹牛？</span><br />跑仓库里的 verify_release.py --chain-only，20 秒逐字节比对 13 颗电路，不用信任任何人。</li>
        </ul>
      </Section>

      <footer className="border-t border-zinc-800 py-8 text-center text-xs text-zinc-600">
        本页所有链上数据可用 independent_audit.py 独立复核 ·{" "}
        <Link to="/" className="underline hover:text-amber-400">返回首页</Link>
      </footer>
    </main>
  );
}
