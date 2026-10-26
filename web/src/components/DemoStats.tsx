import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Smartphone } from "lucide-react";
import { useState } from "react";
import Segmented from "./Segmented";

interface Session { "başlangıç": string; "süre_sn": number; istek: number; mobil: boolean; "bölümler": string[]; "örnek": number; "kayıt": number }
interface Stats {
  configured: boolean; oturum?: number; bot_oturum?: number; "tekil_ziyaretçi_günlük_toplam"?: number; "mobil_%"?: number;
  "süre_medyan_sn"?: number; "süre_ort_sn"?: number; "1dk+_kalan"?: number; "5dk+_kalan"?: number; "örnek_deneyen"?: number;
  "kayıt_ekleyen"?: number; "bölümler"?: Record<string, number>; "günlük"?: Record<string, { oturum: number; "ziyaretçi": number }>;
  son_oturumlar?: Session[];
}

const LABELS: Record<string, string> = {
  ozet: "Özet", islemler: "İşlemler", planlama: "Planlama", istatistik: "İstatistik", zekat: "Zekât", hesaplar: "Hesaplar",
  ekstreler: "Ekstreler", fiyatlar: "Fiyatlar", tasarruf: "Tasarruf", borclar: "Borçlar", transferler: "Transferler",
  ornek: "Örnek dosya", kayit: "Kayıt ekleme",
};
const dur = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} dk ${s % 60 ? `${s % 60} sn` : ""}` : `${s} sn`);

/** Ayarlar (yönetici): canlı demo kullanımı. IP saklanmaz; ziyaretçi günlük değişen parmak iziyle sayılır. */
export default function DemoStats() {
  const [days, setDays] = useState("7");
  const q = useQuery({ queryKey: ["demo-stats", days], retry: false,
    queryFn: () => fetch(`/api/admin/demo-stats?days=${days}`).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() as Promise<Stats>; }) });
  const s = q.data;
  if (q.error) return <section className="card p-4 text-sm text-red-600">Demo istatistikleri: {(q.error as Error).message}</section>;
  if (!s?.configured) return null;
  const tiles: [string, string][] = [
    ["Oturum", String(s.oturum ?? 0)], ["Ziyaretçi (günlük tekil)", String(s["tekil_ziyaretçi_günlük_toplam"] ?? 0)],
    ["Ortanca süre", dur(s["süre_medyan_sn"] ?? 0)], ["1 dk+ kalan", String(s["1dk+_kalan"] ?? 0)],
    ["Örnek deneyen", String(s["örnek_deneyen"] ?? 0)], ["Mobil", `%${s["mobil_%"] ?? 0}`],
  ];
  const areas = Object.entries(s["bölümler"] ?? {});
  const max = Math.max(1, ...areas.map(([, n]) => n));
  return (
    <section className="card space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><FlaskConical size={18} /> Canlı demo</h2>
        <div className="w-56"><Segmented value={days} onChange={setDays}
          options={[{ value: "1", label: "Bugün" }, { value: "7", label: "7 gün" }, { value: "30", label: "30 gün" }]} /></div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {tiles.map(([k, v]) => (
          <div key={k} className="rounded-lg bg-stone-50 p-2 dark:bg-stone-800/50">
            <div className="text-xs text-stone-500">{k}</div><div className="font-semibold tabular-nums">{v}</div>
          </div>
        ))}
      </div>
      {areas.length > 0 && (
        <div className="space-y-1 text-sm">
          <div className="text-xs text-stone-500">Gezilen bölümler (kaç oturumda)</div>
          {areas.map(([a, n]) => (
            <div key={a} className="flex items-center gap-2">
              <span className="w-28 shrink-0">{LABELS[a] ?? a}</span>
              <span className="h-2 rounded bg-blue-500" style={{ width: `${(n / max) * 60}%` }} />
              <span className="tabular-nums text-stone-500">{n}</span>
            </div>
          ))}
        </div>
      )}
      {!!s.son_oturumlar?.length && (
        <details className="text-sm">
          <summary className="cursor-pointer text-stone-600 dark:text-stone-400">Son oturumlar ({s.son_oturumlar.length})</summary>
          <ul className="mt-2 divide-y divide-stone-100 dark:divide-stone-800">
            {s.son_oturumlar.map((o) => (
              <li key={o["başlangıç"] + o.istek} className="flex flex-wrap items-center gap-x-3 py-1.5 text-xs">
                <span className="tabular-nums">{o["başlangıç"].slice(5)}</span>
                <span className="tabular-nums font-medium">{dur(o["süre_sn"])}</span>
                {o.mobil && <Smartphone size={12} className="text-stone-400" />}
                <span className="text-stone-500">{o["bölümler"].map((b) => LABELS[b] ?? b).join(", ") || "—"}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="text-xs text-stone-500">
        Oturum = ziyaretçinin 1 saatlik demo kopyası. IP saklanmaz; ziyaretçi günlük değişen bir parmak iziyle sayılır.
        Botlar ({s.bot_oturum ?? 0}) hariç.
      </p>
    </section>
  );
}
