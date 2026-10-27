import { useQueryClient } from "@tanstack/react-query";
import { Bell, FlaskConical, LogOut, Server, Timer } from "lucide-react";
import { useEffect, useState } from "react";
import { inApp, toLauncher } from "./AppServer";
import { useDemo } from "./hooks";
import Waitlist from "./Waitlist";

const REPO = "https://github.com/alameddinc/hesapkitap";

/** Demo şeridi: uydurma veri, kalan süre (bitince temiz bir kopya), kendi sunucuna kur. */
export default function DemoBanner() {
  const demo = useDemo().data;
  const qc = useQueryClient();
  const [now, setNow] = useState(() => Date.now() / 1000);
  // README / paylaşımdaki "?haberver" bağlantısı formu doğrudan açar
  const [wl, setWl] = useState(() => new URLSearchParams(window.location.search).has("haberver"));
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  if (!demo) return null;
  const left = Math.max(0, Math.round(demo.expires_at - now));
  const restart = async () => {
    await fetch("/api/demo/reset", { method: "POST" });
    qc.clear();
    window.location.reload();
  };
  const mm = String(Math.floor(left / 60)).padStart(2, "0");
  const ss = String(left % 60).padStart(2, "0");
  return (
    <>
      <div className="relative z-50 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 bg-blue-600 px-3 py-1.5 pt-[max(0.375rem,env(safe-area-inset-top))] text-xs text-white sm:text-sm">
        <span className="flex items-center gap-1 font-medium"><FlaskConical size={14} /> Demo · uydurma verilerle</span>
        {left > 0 ? (
          <span className="flex items-center gap-1 tabular-nums" title="Süre bitince yeni ve temiz bir demo başlar">
            <Timer size={14} /> {mm}:{ss}
          </span>
        ) : (
          <button className="rounded bg-white/20 px-2 py-0.5 font-medium" onClick={restart}>Süre doldu · yeni demo başlat</button>
        )}
        {inApp() ? (
          <button className="flex items-center gap-1 rounded bg-white px-2 py-0.5 font-medium text-blue-700" onClick={toLauncher}>
            <LogOut size={14} /> Demodan çık</button>
        ) : (
          <a className="flex items-center gap-1 rounded bg-white px-2 py-0.5 font-medium text-blue-700" href={`${REPO}#-hızlı-kurulum`}
            target="_blank" rel="noreferrer"><Server size={14} /> Kendi sunucuna kur</a>
        )}
        <button className="flex items-center gap-1 rounded bg-amber-300 px-2 py-0.5 font-medium text-stone-900" onClick={() => setWl(true)}>
          <Bell size={14} /> Kurulumsuz sürüm: haber ver</button>
      </div>
      {wl && <Waitlist onClose={() => setWl(false)} />}
    </>
  );
}
