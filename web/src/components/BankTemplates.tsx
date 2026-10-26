import { useQuery } from "@tanstack/react-query";
import { Check, ExternalLink, Layers, RefreshCw, X } from "lucide-react";
import { dateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Spec { kind?: "account" | "card"; bank: string; keywords: string[]; date: { sep: string; order: string }; amount_idx: number;
  balance_idx?: number; scope?: string; num?: string; credit?: string }
interface Tpl { id: number; bank: string; spec: Spec; source: "learned" | "community"; uses: number; created_at: string; shared_at: string | null }
interface Sub { id: number; bank: string; spec: Spec; count: number; status: "bekliyor" | "onaylandi" | "reddedildi"; received_at: string }
interface Data { templates: Tpl[]; sharing: boolean; is_hub: boolean; submissions: Sub[] }

const call = async (method: string, url: string, body?: unknown) => {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j;
};
const slug = (s: string) => s.toLowerCase().replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g").replace(/ü/g, "u")
  .replace(/ö/g, "o").replace(/ç/g, "c").replace(/[^a-z0-9]+/g, "-");
// GitHub'da dosya oluşturma ekranı (önceden doldurulmuş): onaylanan şablon depoya eklenir, tüm sunucular çeker
const ghNew = (s: Sub) => `https://github.com/alameddinc/hesapkitap/new/main/templates?filename=${slug(s.bank)}-${s.spec.kind === "card" ? "kart" : "hesap"}-${s.id}.json`
  + `&value=${encodeURIComponent(JSON.stringify([s.spec], null, 2))}`;

/** Ayarlar: banka dökümü şablonları (yapay zekâsız okuma), topluluk paylaşımı, hub'da gelen şablonların onayı. */
export default function BankTemplates({ isAdmin }: { isAdmin: boolean }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["bank-templates"], queryFn: () => call("GET", "/api/templates") as Promise<Data> });
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const sharing = useSave((on: boolean) => call("PUT", "/api/templates/sharing", { on }));
  const sync = useSave(() => call("POST", "/api/templates/sync"), (r: { added: number }) => ui.toast(`${r.added} yeni şablon`));
  const review = useSave((a: { id: number; status: string }) => call("POST", `/api/templates/submissions/${a.id}/${a.status}`));
  const d = q.data;
  if (!d) return null;
  const pending = d.submissions.filter((s) => s.status === "bekliyor");
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Layers size={18} /> Banka şablonları</h2>
      <p className="text-sm text-stone-500">
        Hesap dökümlerini ve kart ekstrelerini yapay zekâsız okumak için öğrenilen kurallar. Yeni bir banka gelince kural
        doğrulanırsa (dökümde bakiye zinciri, ekstrede "önceki borç − ödemeler + harcamalar = dönem borcu") kendiliğinden
        öğrenilir; topluluk deposundaki kurallar da günde bir kez çekilir.
      </p>
      {isAdmin && (
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={d.sharing} onChange={(e) => sharing.mutate(e.target.checked, onErr)} />
          <span><b>Uygulamanın gelişmesine destek ol</b> — yeni öğrenilen kuralları toplulukla paylaş.
            <span className="block text-xs text-stone-500">Gönderilen sadece banka adı ve satır düzeni (tarih ve sayı biçimi, hangi sütunun tutar/bakiye olduğu); isim, IBAN, kart no, tutar, açıklama gitmez.</span>
          </span>
        </label>
      )}
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {d.templates.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span><b>{t.bank}</b> <span className="text-xs text-stone-500">· {t.spec.kind === "card" ? "kart ekstresi" : "hesap dökümü"} · {t.source === "community" ? "topluluktan" : "bu sunucuda öğrenildi"} · {t.uses} kez kullanıldı
              {t.shared_at && ` · paylaşıldı ${dateTR(t.shared_at.slice(0, 10))}`}</span></span>
            <code className="text-xs text-stone-500">{t.spec.date.order}{t.spec.date.sep} · tutar #{t.spec.amount_idx}
              {t.spec.kind === "card" ? ` · ${t.spec.num} · ödeme ${t.spec.credit === "minus" ? "−" : "+"}` : ` · bakiye #${t.spec.balance_idx}`}</code>
          </li>
        ))}
        {!d.templates.length && <li className="py-2 text-stone-500">Henüz şablon yok.</li>}
      </ul>
      <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" onClick={() => sync.mutate(undefined, onErr)}>
        <RefreshCw size={14} /> Topluluktan şimdi çek
      </button>

      {d.is_hub && isAdmin && (
        <div className="space-y-2 border-t border-stone-100 pt-3 dark:border-stone-800">
          <h3 className="text-sm font-medium">Gelen şablonlar {pending.length > 0 && <span className="text-amber-600">· {pending.length} onay bekliyor</span>}</h3>
          <ul className="space-y-2 text-xs">
            {d.submissions.map((s) => (
              <li key={s.id} className="rounded-lg border border-stone-200 p-2 dark:border-stone-700">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span><b>{s.bank}</b> · {s.count} sunucudan · {dateTR(s.received_at.slice(0, 10))} · {s.status}</span>
                  <span className="flex gap-1">
                    {s.status !== "onaylandi" && <button className="btn-ghost px-2 py-0.5" onClick={() => review.mutate({ id: s.id, status: "onaylandi" }, onErr)}><Check size={13} /> Onayla</button>}
                    {s.status !== "reddedildi" && <button className="btn-ghost px-2 py-0.5" onClick={() => review.mutate({ id: s.id, status: "reddedildi" }, onErr)}><X size={13} /> Reddet</button>}
                    {s.status === "onaylandi" && <a className="btn-primary px-2 py-0.5" href={ghNew(s)} target="_blank" rel="noreferrer"><ExternalLink size={13} /> GitHub'a ekle</a>}
                  </span>
                </div>
                <pre className="mt-1 overflow-x-auto rounded bg-stone-50 p-2 dark:bg-stone-900">{JSON.stringify(s.spec)}</pre>
              </li>
            ))}
            {!d.submissions.length && <li className="text-stone-500">Henüz gelen şablon yok.</li>}
          </ul>
        </div>
      )}
    </section>
  );
}
