import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, CheckCircle2, RefreshCw } from "lucide-react";
import { dateTR } from "../format";

interface V { current: string | null; latest: string | null; latest_date?: string; behind: number | null; changes: string[];
  platform: "render" | "railway" | "fly" | "docker"; error?: string }

const HOW: Record<V["platform"], React.ReactNode> = {
  render: <>Render panelinde servis → <b>Manual Deploy → Deploy latest commit</b>.</>,
  railway: <>Railway'de servis → <b>Deployments → Redeploy</b> (ya da fork'unu güncelle).</>,
  fly: <>Bilgisayarında: <code>git pull && fly deploy</code></>,
  docker: <>Sunucuda: <code>docker compose pull && docker compose up -d</code> (CasaOS/Portainer/Synology'de "Update").
    Kaynaktan kurduysan: <code>git pull && docker compose up -d --build</code></>,
};

/** Ayarlar (yönetici): GitHub'da yeni sürüm var mı; varsa ne değişti ve nasıl güncellenir. */
export default function UpdateNotice() {
  const q = useQuery({ queryKey: ["version"], queryFn: () => fetch("/api/version").then((r) => r.json() as Promise<V>), staleTime: 3600_000 });
  const refresh = useQuery({ queryKey: ["version-refresh"], enabled: false,
    queryFn: () => fetch("/api/version?refresh=true").then((r) => r.json() as Promise<V>) });
  const v = refresh.data ?? q.data;
  if (!v || v.latest === undefined) return null;
  const short = (s: string | null) => (s ? s.slice(0, 7) : "?");
  if (v.behind && v.behind > 0) {
    return (
      <section className="card space-y-2 border-amber-300 bg-amber-50/60 p-4 dark:border-amber-800 dark:bg-amber-950/30">
        <h2 className="flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-200"><ArrowUpCircle size={18} /> Yeni sürüm var · {v.behind} değişiklik</h2>
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-stone-700 dark:text-stone-300">
          {v.changes.map((c, i) => <li key={i}>{c}</li>)}
          {v.behind > v.changes.length && <li className="text-stone-500">ve {v.behind - v.changes.length} değişiklik daha</li>}
        </ul>
        <p className="text-sm">Güncellemek için: {HOW[v.platform]} Verilerin silinmez.</p>
        <p className="text-xs text-stone-500">Bu kurulum {short(v.current)} · son sürüm {short(v.latest)}{v.latest_date && ` (${dateTR(v.latest_date.slice(0, 10))})`}</p>
      </section>
    );
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
      {v.behind === 0 ? <><CheckCircle2 size={14} className="text-green-600" /> Güncel sürüm ({short(v.current)})</>
        : v.error ? <>Sürüm kontrolü: {v.error}</>
        : <>Sürüm bilinmiyor (kaynaktan derlenmiş) · son sürüm {short(v.latest)}{v.latest_date && ` · ${dateTR(v.latest_date.slice(0, 10))}`}</>}
      <button className="btn-ghost p-1" aria-label="Sürümü yeniden kontrol et" onClick={() => refresh.refetch()}><RefreshCw size={12} /></button>
    </p>
  );
}
