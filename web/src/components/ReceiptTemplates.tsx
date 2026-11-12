import { useQuery } from "@tanstack/react-query";
import { Receipt } from "lucide-react";
import { send } from "../lib/apiError";
import SorguDurumu, { KartIskelet } from "./SorguDurumu";

/** GET /api/admin/receipt-templates: zincir / akaryakıt fişi şablonları. Kişisel veri yok: ad yalnız bilinen zincirse. */
interface Tpl {
  id: number; name: string; signature: "VKN" | "unvan"; fuel: boolean; single: boolean; source: string;
  uses: number; fails: number; disabled: boolean; last_used: string | null; created_at: string; shareable: boolean;
}
/** app/template_stats.py overview: belge türü başına yapay zekâsız okuma oranı ve neden kodları (içerik yok). */
interface HitDoc {
  reads: number; free: number; rate: number | null; by_outcome: Record<string, number>;
  miss_reasons: Record<string, number>; learn: Record<string, number>; since: string | null;
}
interface HitRate { days: number; since: string | null; fis: HitDoc; ekstre: HitDoc; dokum: HitDoc }
interface Overview { templates: Tpl[]; total: number; active: number; uses: number; hit_rate?: HitRate }

const DOC_LABEL: Record<"fis" | "ekstre" | "dokum", string> = { fis: "Fiş", ekstre: "Kart ekstresi", dokum: "Hesap dökümü" };
// yapay zekâya gitme nedenleri (app/receipt_templates.py MISS_REASONS, app/parsers _template_miss, app/api/main.py _count_receipt)
const REASON: Record<string, string> = {
  no_text: "telefon metni yok", pdf: "PDF fiş", no_template: "şablon yok", no_structure: "düzen farklı",
  no_total: "toplam satırı bulunamadı", no_items: "kalem yok", qty: "miktar tutmadı", sum: "kalem toplamı tutmadı",
  ocr_digit: "metinde rakam karışmış", date: "tarih okunamadı", unreadable: "okunamadı", not_tried: "denenmedi",
  text_cid: "PDF yazı tipi kodlu", unverified: "şablon tuttu, denklem tutmadı", error: "hata",
};
// öğrenme sonuçları (receipt_templates.learn)
const LEARN: Record<string, string> = {
  learned: "öğrenildi", learned_digit: "öğrenildi (tek rakam farkıyla)", known: "zaten vardı", ai_sum: "yapay zekâ kalemleri tutmadı",
  ai_total_not_in_text: "toplam metinde yok", ai_low_conf: "düşük güven", ai_no_date: "tarih yok", ai_no_items: "kalem yok",
  ai_no_total: "toplam yok", not_receipt: "fiş değil", vkn_conflict: "VKN çelişkisi", no_sig: "imza yok",
  date_fmt: "tarih metinde yok", no_anchor: "toplam etiketi yok", no_match: "düzen çıkarılamadı", fuel_qty: "litre satırı bağlanmadı",
  error: "hata",
};

const top = (m: Record<string, number>, names: Record<string, string>) =>
  Object.entries(m).slice(0, 4).map(([k, n]) => `${names[k] ?? k} ${n}`).join(" · ");

/** "Son 30 gün: fiş %X şablonla" göstergesi (yapay zekâsız okuma oranı). */
function HitRateRow({ h }: { h: HitRate }) {
  const docs = Object.keys(DOC_LABEL) as (keyof typeof DOC_LABEL)[];
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {docs.map((d) => {
          const x = h[d];
          return (
            <div key={d} className="min-w-0 rounded-lg border border-stone-200 p-2.5 dark:border-stone-800">
              <div className="text-xs text-stone-500">{DOC_LABEL[d]} · şablonla</div>
              <div className="text-xl font-semibold tabular-nums">{x.rate == null ? "—" : `%${x.rate}`}</div>
              <div className="text-xs text-stone-500 tabular-nums">{x.free} / {x.reads} okuma</div>
              {Object.keys(x.miss_reasons).length > 0 && (
                <div className="mt-1 break-words text-xs text-stone-500">Yapay zekâya: {top(x.miss_reasons, REASON)}</div>
              )}
              {d === "fis" && Object.keys(x.learn).length > 0 && (
                <div className="mt-1 break-words text-xs text-stone-500">Öğrenme: {top(x.learn, LEARN)}</div>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-xs text-stone-500">
        Son {h.days} gün; yapay zekâsız = şablon ya da bankaya özel okuyucu (₺0).
        {h.since ? ` Ölçüm ${h.since} tarihinden beri.` : " Henüz ölçüm yok."}
      </p>
    </div>
  );
}

const day = (s: string | null) => (s ? s.slice(0, 16).replace("T", " ") : "—");

/** Ayarlar (yönetici): fiş şablonları — yapay zekâya gitmeden okunan fişler, şablon başına isabet / hata. */
export default function ReceiptTemplates() {
  const q = useQuery({ queryKey: ["admin-receipt-templates"], retry: false,
    queryFn: () => send<Overview>("/api/admin/receipt-templates") });
  const s = q.data;
  if (!s) return <SorguDurumu q={q} etiket="Fiş şablonları yükleniyor"><KartIskelet satir={2} /></SorguDurumu>;
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Receipt size={18} /> Fiş şablonları</h2>
      <p className="text-xs text-stone-500">
        Aynı işyerinin fişi telefon metninden yapay zekâsız okunur. {s.total} şablon, {s.active} açık, toplam {s.uses} okuma.
      </p>
      {s.hit_rate && <HitRateRow h={s.hit_rate} />}
      {s.templates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-stone-500">
              <tr><th className="py-1 pr-2">Şablon</th><th className="pr-2">İmza</th><th className="pr-2 text-right">İsabet</th>
                <th className="pr-2 text-right">Hata</th><th className="pr-2">Son kullanım</th><th>Durum</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {s.templates.map((t) => (
                <tr key={t.id}>
                  <td className="py-1.5 pr-2 font-medium">
                    {t.name}{t.fuel && <span className="ml-1 text-stone-500">{t.single ? "· slip" : "· akaryakıt"}</span>}
                  </td>
                  <td className="pr-2">{t.signature}{t.source === "community" ? " · topluluk" : ""}</td>
                  <td className="pr-2 text-right tabular-nums">{t.uses}</td>
                  <td className="pr-2 text-right tabular-nums">{t.fails}</td>
                  <td className="pr-2 tabular-nums text-stone-500">{day(t.last_used)}</td>
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
