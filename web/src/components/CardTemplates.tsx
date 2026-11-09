import { useQuery } from "@tanstack/react-query";
import { CreditCard } from "lucide-react";
import { send } from "../lib/apiError";
import { nedenMetni, nedenSirasi } from "../lib/sablonNeden";
import SorguDurumu, { KartIskelet } from "./SorguDurumu";

/** GET /api/admin/card-templates: kart ekstresi şablonları ve yapay zekâ okumasından öğrenme denemeleri.
 * Kişisel veri yok: banka adı, sayılar ve neden kodları. */
interface Attempt { bank: string; ok: number; failed: number; last_day: string; reasons: Record<string, number> }
interface Tpl { id: number; bank: string; source: string; uses: number; fails: number; disabled: boolean; created_at: string }
interface Overview { days: number; attempts: Attempt[]; templates: Tpl[] }

/** Ayarlar (yönetici) → Şablonlar: kart ekstresi şablonu öğrenme — banka başına deneme, sonuç ve neden. */
export default function CardTemplates() {
  const q = useQuery({ queryKey: ["admin-card-templates"], retry: false,
    queryFn: () => send<Overview>("/api/admin/card-templates") });
  const s = q.data;
  if (!s) return <SorguDurumu q={q} etiket="Kart şablonları yükleniyor"><KartIskelet satir={2} /></SorguDurumu>;
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><CreditCard size={18} /> Kart ekstresi şablonları</h2>
      <p className="text-xs text-stone-500">
        Yapay zekânın okuduğu ekstreden bankanın düzeni öğrenilir; sonraki ekstre yapay zekâsız okunur. Şablon yalnız
        aynı ekstreyi aynen okuyup denklemi tutturursa kaydedilir. Son {s.days} gün:
      </p>
      {s.attempts.length === 0 ? (
        <p className="text-sm text-stone-500">Bu dönemde öğrenme denemesi yok.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-stone-500">
              <tr><th className="py-1 pr-2">Banka</th><th className="pr-2 text-right">Başarılı</th>
                <th className="pr-2 text-right">Başarısız</th><th className="pr-2">Neden</th><th>Son</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {s.attempts.map((a) => (
                <tr key={a.bank} className="align-top">
                  <td className="py-1.5 pr-2 font-medium">{a.bank === "?" ? "tanınmayan" : a.bank}</td>
                  <td className="pr-2 text-right tabular-nums">{a.ok}</td>
                  <td className={`pr-2 text-right tabular-nums ${a.failed ? "text-red-700 dark:text-red-400" : ""}`}>{a.failed}</td>
                  <td className="pr-2">
                    <ul className="space-y-0.5">
                      {nedenSirasi(a.reasons).map(([k, n]) => (
                        <li key={k} title={k}>{nedenMetni(k)} <span className="tabular-nums text-stone-500">×{n}</span></li>
                      ))}
                    </ul>
                  </td>
                  <td className="whitespace-nowrap tabular-nums text-stone-500">{a.last_day}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {s.templates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-stone-500">
              <tr><th className="py-1 pr-2">Şablon</th><th className="pr-2 text-right">İsabet</th>
                <th className="pr-2 text-right">Hata</th><th className="pr-2">Eklendi</th><th>Durum</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {s.templates.map((t) => (
                <tr key={t.id}>
                  <td className="py-1.5 pr-2 font-medium">{t.bank}{t.source === "community" ? <span className="text-stone-500"> · topluluk</span> : ""}</td>
                  <td className="pr-2 text-right tabular-nums">{t.uses}</td>
                  <td className="pr-2 text-right tabular-nums">{t.fails}</td>
                  <td className="whitespace-nowrap pr-2 tabular-nums text-stone-500">{(t.created_at ?? "").slice(0, 10)}</td>
                  <td>{t.disabled
                    ? <span className="rounded bg-red-100 px-1.5 text-red-800 dark:bg-red-950 dark:text-red-300">kapalı</span>
                    : <span className="rounded bg-green-100 px-1.5 text-green-800 dark:bg-green-950 dark:text-green-300">açık</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
