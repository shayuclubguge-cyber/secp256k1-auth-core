import Hero from "../sections/Hero";
import Verifier from "../sections/Verifier";
import Architecture from "../sections/Architecture";
import Economics from "../sections/Economics";
import Footer from "../sections/Footer";

export default function Home() {
  return (
    <main className="min-h-screen bg-zinc-950 font-sans text-zinc-100 antialiased">
      <Hero />
      <Verifier />
      <Architecture />
      <Economics />
      <Footer />
    </main>
  );
}
