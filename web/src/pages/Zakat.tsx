import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { errorText } from "../lib/apiError";
import { ChevronDown, Info, Moon, Plus, RefreshCw, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { useSave } from "../components/hooks";
import AssetForm from "../components/AssetForm";
import Sadaka from "../components/Sadaka";
import { ZakatOff } from "../components/ZakatConsent";
import Segmented from "../components/Segmented";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";

interface Line { id?: number; acquired?: string | null; label: string; qty?: number; unit?: string; value: number | null; exempt?: boolean; source: string; note?: string | null }
interface Payment { id: number; year_start: string; pay_date: string; amount: number; note: string | null }
interface Person {
  id: number; name: string; is_self: boolean; is_minor?: boolean; plus: Line[];
  check?: { date: string; hijri: string; days_left: number; zakat_now: number; paid: number; difference: number } | null; minus: Line[]; gross: number; deduct: number; total: number;
  above_nisab: boolean; estimate: number; suggest_start: string | null; payments: Payment[]; missing_prices: string[];
  hawl: null | { start: string; start_hijri: string; due: string; due_hijri: string; days_left: number; progress: number;
    zakat: number; paid: number; remaining: number };
}
interface ZakatData {
  nisab: number | null; nisab_grams: number; gram_price: number | null; today_hijri: string;
  settings: { madhhab: "hanefi" | "safii"; include_app: boolean; include_receivables: boolean };
  prices: { updated: string | null; source: string | null; manual_gram: number | null; list: Record<string, number> };
  kinds: Record<string, { label: string; unit: string }>;
  people: Person[];
}

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errorText(j, r.status));
  return j as T;
};
const today = () => new Date().toISOString().slice(0, 10);

interface HistYear {
  start: string; end: string; end_hijri: string; error?: string; assets?: number; bank?: number; deduct?: number; total?: number;
  nisab?: number; above_nisab?: boolean; start_below?: boolean; zakat?: number; paid?: number; remaining?: number; unknown_accounts?: string[]; partial?: boolean;
}

/** Geçmiş zekât yılları: o yılın sonunda varlık, nisap, zekât, ödenen; ödenmemişse kaza. */
function History({ p }: { p: Person }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["zakat-history", p.id, p.hawl?.start],
    queryFn: () => json<{ years: HistYear[]; need_start: boolean; total_due: number; notes: string[] }>("GET", `/api/zakat/people/${p.id}/history`) });
  const pay = useSave((a: { y: HistYear; amount: number }) => json("POST", `/api/zakat/people/${p.id}/payments`,
    { amount: a.amount, year_start: a.y.start, note: "Geçmiş yıl" }), () => ui.toast("Ödeme o yıla kaydedildi"));
  const d = q.data;
  if (q.isLoading) return <p className="text-xs text-stone-500">Geçmiş fiyatlar alınıyor…</p>;
  if (!d) return null;
  if (d.need_start) return <p className="text-xs text-stone-500">Önce zekât yılının başladığı günü gir.</p>;
  return (
    <div className="space-y-2">
      {d.total_due > 0 && (
        <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          Ödenmemiş görünen (kaza) zekât toplamı: <b>{tl(d.total_due)}</b>. Daha önce ödediysen ilgili yıla "Ödedim" de.
        </p>
      )}
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {d.years.map((y) => (
          <li key={y.end} className="py-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium">{dateTR(y.start)} → {dateTR(y.end)} <span className="text-xs font-normal text-stone-500">({y.end_hijri})</span></span>
              {y.error ? <span className="text-xs text-red-600">{y.error}</span>
                : !y.above_nisab ? <span className="rounded bg-stone-100 px-1.5 py-0.5 text-xs text-stone-600 dark:bg-stone-800">{y.start_below ? "yıl başında nisap altı" : "nisap altı"}</span>
                : (y.remaining ?? 0) <= 0 ? <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-800 dark:bg-green-950 dark:text-green-300">ödendi</span>
                : <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">ödenmedi · {tl(y.remaining ?? 0)}</span>}
            </div>
            {!y.error && (
              <div className="mt-0.5 text-xs text-stone-500">
                Yıl sonunda {tl(y.total ?? 0)}{" "}
                ({[y.assets ? `altın/döviz ${tl(y.assets)}` : null, y.bank ? `banka ${tl(y.bank)}` : null,
                  y.deduct ? `düşülen ${tl(y.deduct)}` : null].filter(Boolean).join(" · ") || "varlık yok"})
                {" "}· nisap {tl(y.nisab ?? 0)}
                {y.above_nisab && <> · zekât {tl(y.zakat ?? 0)}{(y.paid ?? 0) > 0 && <> · ödenen {tl(y.paid ?? 0)}</>}</>}
                {y.partial && <span className="block text-amber-600">Yılın başında veri yok: yıl daha geç başlamış olabilir.</span>}
                {!!y.unknown_accounts?.length && <span className="block text-amber-600">O tarihte hareketi bilinmeyen: {y.unknown_accounts.join(", ")}</span>}
              </div>
            )}
            {!y.error && (y.remaining ?? 0) > 0 && (
              <button className="btn-ghost mt-1 border border-stone-300 px-2 py-0.5 text-xs dark:border-stone-700" onClick={() => {
                const v = window.prompt(`${dateTR(y.end)}'de biten yıl için ödediğin tutar:`, String(y.remaining).replace(".", ","));
                if (v && parseAmount(v) > 0) pay.mutate({ y, amount: parseAmount(v) }, { onError: (e) => ui.toast(e.message) });
              }}>Ödedim</button>
            )}
          </li>
        ))}
      </ul>
      {d.notes.map((n) => <p key={n} className="text-xs text-stone-500">• {n}</p>)}
      <p className="text-xs text-stone-500">• Ödenmeyen zekât, Hanefî'ye göre borç olarak kalır ve sonraki yılın hesabından düşülür. Kesin karar için müftülüğe danış.</p>
    </div>
  );
}

function PersonCard({ p, d }: { p: Person; d: ZakatData }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payAmt, setPayAmt] = useState("");
  const [hist, setHist] = useState(false);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const setStart = useSave((v: string | null) => json("PATCH", `/api/zakat/people/${p.id}`, { hawl_start: v }));
  const setMinor = useSave((v: boolean) => json("PATCH", `/api/zakat/people/${p.id}`, { is_minor: v }));
  const setCheck = useSave((v: string | null) => json("PATCH", `/api/zakat/people/${p.id}`, { check_date: v }));
  const nextYear = useSave(() => json("POST", `/api/zakat/people/${p.id}/next-year`), () => ui.toast("Yeni zekât yılı başladı"));
  const pay = useSave(() => json("POST", `/api/zakat/people/${p.id}/payments`, { amount: parseAmount(payAmt), pay_date: today() }),
    () => { setPaying(false); setPayAmt(""); ui.toast("Ödeme kaydedildi. Allah kabul etsin."); });
  const delPay = useSave((id: number) => json("DELETE", `/api/zakat/payments/${id}`));
  const delAsset = useSave((id: number) => json("DELETE", `/api/zakat/assets/${id}`));
  const delPerson = useSave(() => json("DELETE", `/api/zakat/people/${p.id}`));
  const h = p.hawl;
  const yearPays = h ? p.payments.filter((x) => x.year_start === h.start) : [];

  return (
    <section className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{p.name}</h2>
          <p className="text-xs text-stone-500">Zekâta tabi net: <b className="tabular-nums">{tl(p.total)}</b></p>
        </div>
        <span className={cx("rounded px-2 py-0.5 text-xs", p.above_nisab ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
          : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400")}>
          {p.is_minor ? "Çocuk · zekât yok" : p.above_nisab ? "Nisap üstü" : "Nisap altı"}
        </span>
      </div>

      {d.nisab != null && (
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800" title="Nisaba göre">
          <div className={cx("h-full rounded-full", p.above_nisab ? "bg-green-600" : "bg-amber-500")}
            style={{ width: `${Math.min(100, (p.total / d.nisab) * 100)}%` }} />
        </div>
      )}

      {!p.is_self && (
        <label className="mt-2 flex cursor-pointer items-center gap-2 text-xs text-stone-600 dark:text-stone-400">
          <input type="checkbox" checked={!!p.is_minor} onChange={(e) => setMinor.mutate(e.target.checked, onErr)} />
          Çocuk (ergenlik çağına gelmemiş) — Hanefî'ye göre malına zekât düşmez
        </label>
      )}

      {/* zekât yılı */}
      {p.is_minor ? (
        <p className="mt-3 rounded-lg bg-stone-50 p-3 text-xs text-stone-600 dark:bg-stone-900 dark:text-stone-400">
          Varlıkları takip edilir ama zekât hesaplanmaz. Ergenlik çağına girince işareti kaldır; zekât yılı nisaba ulaştığı günden başlar.
        </p>
      ) : (
      <div className="mt-3 rounded-lg bg-stone-50 p-3 text-sm dark:bg-stone-900">
        {h ? (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span>Zekât yılı: {dateTR(h.start)} → <b>{dateTR(h.due)}</b></span>
              <span className="text-xs text-stone-500">{h.start_hijri} → {h.due_hijri}</span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
              <div className="h-full rounded-full bg-blue-600" style={{ width: `${h.progress}%` }} />
            </div>
            <div className="mt-2 text-xs text-stone-600 dark:text-stone-400">
              {h.days_left > 0 ? `${h.days_left} gün kaldı. ` : h.days_left === 0 ? "Yıl bugün doluyor. " : `Yıl ${-h.days_left} gün önce doldu. `}
              {p.above_nisab
                ? <>Bugünkü hesapla zekâtın <b>{tl(h.zakat)}</b>{h.paid > 0 && <> · ödenen {tl(h.paid)} · kalan <b>{tl(h.remaining)}</b></>}</>
                : "Şu an nisabın altındasın; yıl sonunda da altındaysan zekât düşmez."}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {paying ? (
                <>
                  <input className="input w-36 text-right" inputMode="decimal" placeholder="Tutar" autoFocus value={payAmt} onChange={(e) => setPayAmt(e.target.value)} />
                  <button className="btn-primary px-3 py-1" disabled={!(parseAmount(payAmt) > 0)} onClick={() => pay.mutate(undefined, onErr)}>Kaydet</button>
                  <button className="btn-ghost px-2 py-1" onClick={() => setPaying(false)}>Vazgeç</button>
                </>
              ) : (
                <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                  onClick={() => { setPayAmt(h.remaining > 0 ? String(h.remaining) : ""); setPaying(true); }}>Ödeme ekle</button>
              )}
              {h.days_left <= 0 && (
                <button className="btn-primary px-2 py-1 text-xs"
                  onClick={() => void ask("Yeni zekât yılı başlasın mı?\n\nBu yılın ödemeleri geçmişte kalır.", { confirm: "Yeni yıl başlat" }).then((ok) => { if (ok) nextYear.mutate(undefined, onErr); })}>
                  Yeni yılı başlat
                </button>
              )}
            </div>
            <div className="mt-3 border-t border-stone-200 pt-2 text-xs text-stone-600 dark:border-stone-700 dark:text-stone-400">
              {p.check ? (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    Ek kontrol: <b>{dateTR(p.check.date)}</b> ({p.check.hijri}) · {p.check.days_left >= 0 ? `${p.check.days_left} gün kaldı` : "geçti"} ·
                    bugünkü hesapla fark <b>{tl(p.check.difference)}</b>
                  </span>
                  <button className="btn-ghost px-1.5 py-0.5" onClick={() => setCheck.mutate(null, onErr)}>Kaldır</button>
                </div>
              ) : (
                <label className="flex flex-wrap items-center gap-2">
                  Ek kontrol günü (ops.)
                  <input type="date" className="input w-40 px-2 py-1 text-xs" onChange={(e) => e.target.value && setCheck.mutate(e.target.value, onErr)} />
                  <span className="w-full text-stone-500">Yıl başı belirsizse ikinci olası zekât günü: o gün yeniden hesaplanır, ödenenden fazlası "fark" olarak hatırlatılır.</span>
                </label>
              )}
            </div>
            {yearPays.length > 0 && (
              <ul className="mt-2 space-y-1 text-xs">
                {yearPays.map((x) => (
                  <li key={x.id} className="flex items-center justify-between gap-2">
                    <span>{dateTR(x.pay_date)} {x.note && `· ${x.note}`}</span>
                    <span className="flex items-center gap-1 tabular-nums">{tl(x.amount)}
                      <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Ödemeyi sil"
                        onClick={() => void ask("Ödeme silinsin mi?").then((ok) => { if (ok) delPay.mutate(x.id); })}><Trash2 size={12} /></button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <div className="space-y-2 text-xs text-stone-600 dark:text-stone-400">
            <p>
              Zekât yılı, mal varlığının nisaba ulaştığı gün başlar ve bir kameri yıl (354 gün) sürer.
              {p.above_nisab ? " Şu an nisap üstündesin." : ""} Başladığı günü biliyorsan gir; bilmiyorsan bugünü ya da her yıl verdiğin günü (ör. Ramazan'ın 1'i) seç.
            </p>
            {p.suggest_start && (
              <div className="rounded-md border border-blue-200 bg-blue-50 p-2 dark:border-blue-900 dark:bg-blue-950/30">
                Girdiğin tarihlere göre nisaba yaklaşık <b>{dateTR(p.suggest_start)}</b>'de ulaşmışsın (bugünkü fiyatla).
                <button className="btn-primary ml-2 px-2 py-0.5 text-xs" onClick={() => setStart.mutate(p.suggest_start, onErr)}>Bu tarihten başlat</button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <input type="date" className="input w-44" max={today()} onChange={(e) => e.target.value && setStart.mutate(e.target.value, onErr)} />
              <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700" onClick={() => setStart.mutate(today(), onErr)}>Bugünden başlat</button>
            </div>
          </div>
        )}
      </div>
      )}

      {p.missing_prices.length > 0 && <p className="mt-2 text-xs text-amber-600">Fiyatı bulunamadı: {p.missing_prices.join(", ")}</p>}

      <button className="btn-ghost mt-2 px-1 py-1 text-xs" onClick={() => setOpen(!open)}>
        <ChevronDown size={14} className={cx("transition-transform", open && "rotate-180")} /> Döküm ({p.plus.length + p.minus.length} kalem)
      </button>
      {open && (
        <div className="space-y-2 text-sm">
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {p.plus.map((x, i) => (
              <li key={`p${i}`} className="flex items-center justify-between gap-2 py-1.5">
                <span className={cx("min-w-0", x.exempt && "text-stone-400")}>
                  {x.label}{x.qty != null && <span className="text-xs text-stone-500"> · {x.qty} {x.unit}</span>}
                  {x.acquired && <span className="text-xs text-stone-500"> · {dateTR(x.acquired)}</span>}
                  {x.note && <span className="text-xs text-stone-500"> · {x.note}</span>}
                  {x.source !== "varlik" && <span className="text-xs text-stone-400"> · uygulamadan</span>}
                </span>
                <span className="flex items-center gap-1 tabular-nums">{x.value != null ? tl(x.value) : "?"}
                  {x.id && <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Sil"
                    onClick={() => void ask(`${x.label} silinsin mi?`).then((ok) => { if (ok) delAsset.mutate(x.id!); })}><Trash2 size={12} /></button>}
                </span>
              </li>
            ))}
            {p.minus.map((x, i) => (
              <li key={`m${i}`} className="flex items-center justify-between gap-2 py-1.5">
                <span>{x.label} <span className="text-xs text-stone-400">· düşülür</span></span>
                <span className="tabular-nums text-red-600">−{tl(x.value ?? 0)}</span>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" onClick={() => setAdding(true)}><Plus size={14} /> Altın / döviz / para ekle</button>
            {h && <button className="btn-ghost px-2 py-1 text-xs" onClick={() => {
              const v = window.prompt("Zekât yılının başladığı gün (YYYY-AA-GG):", h.start);
              if (v) setStart.mutate(v, onErr);
            }}>Yıl başlangıcını değiştir</button>}
            {!p.is_self && <button className="btn-danger px-2 py-1 text-xs"
              onClick={() => void ask(`${p.name} ve varlıkları silinsin mi?`).then((ok) => { if (ok) delPerson.mutate(undefined, onErr); })}>Kişiyi sil</button>}
          </div>
        </div>
      )}
      {!open && (
        <button className="btn-ghost ml-1 px-2 py-1 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Varlık ekle</button>
      )}
      {h && !p.is_minor && (
        <>
          <button className="btn-ghost ml-1 px-2 py-1 text-xs" onClick={() => setHist(!hist)}>
            <ChevronDown size={14} className={cx("transition-transform", hist && "rotate-180")} /> Geçmiş yıllar (ödendi mi?)
          </button>
          {hist && <History p={p} />}
        </>
      )}
      {adding && <AssetForm people={d.people} kinds={d.kinds} personId={p.id} onClose={() => setAdding(false)} />}
    </section>
  );
}

const TAB_KEY = "hk-zakat-tab";

/** Zekât ve Sadaka: iki sekme. */
export default function Zakat() {
  const [tab, setTab] = useState<"zekat" | "sadaka">(() => { try { return localStorage.getItem(TAB_KEY) === "sadaka" ? "sadaka" : "zekat"; } catch { return "zekat"; } });
  const pick = (t: "zekat" | "sadaka") => { setTab(t); try { localStorage.setItem(TAB_KEY, t); } catch { /* özel mod */ } };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="flex items-center gap-2 text-xl font-semibold"><Moon size={20} /> Zekât ve Sadaka</h1>
        <Segmented value={tab} onChange={pick} options={[{ value: "zekat", label: "Zekât" }, { value: "sadaka", label: "Sadaka" }]} />
      </div>
      {tab === "zekat" ? <ZakatTab /> : <Sadaka />}
    </div>
  );
}

/** Zekât: kişi bazında nisap, kameri yıl, hesap ve ödeme takibi (Diyanet / Hanefî esas). */
function ZakatTab() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["zakat"], queryFn: () => json<ZakatData>("GET", "/api/zakat") });
  const [newPerson, setNewPerson] = useState("");
  const [showRules, setShowRules] = useState(false);
  const [closing, setClosing] = useState(false);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const addPerson = useSave(() => json("POST", "/api/zakat/people", { name: newPerson }), () => setNewPerson(""));
  const settings = useSave((b: object) => json("PUT", "/api/zakat/settings", b));
  const refresh = useSave(() => json("POST", "/api/zakat/prices/refresh"), () => ui.toast("Fiyatlar güncellendi"));
  const d = q.data;
  if (!d) return <p className="text-sm text-stone-500">{q.isError ? "Zekât bilgisi alınamadı" : "Yükleniyor…"}</p>;
  const pl = d.prices.list;

  return (
    <div className="space-y-4">
      <p className="text-sm text-stone-500">Her kişi için ayrı: nisap, kameri yıl, hesap ve ödeme. Bugün {d.today_hijri}.</p>

      <section className="card space-y-2 p-4 text-sm">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span>Nisap: <b>{d.nisab != null ? tl(d.nisab) : "?"}</b> <span className="text-xs text-stone-500">({d.nisab_grams} gr altın)</span></span>
          <span className="flex items-center gap-1 text-xs text-stone-500">
            {d.prices.manual_gram ? "elle girilen fiyat" : d.prices.updated ? `fiyatlar ${d.prices.updated.slice(11, 16)}` : "fiyat alınamadı"}
            <button className="btn-ghost p-1" aria-label="Fiyatları yenile" onClick={() => refresh.mutate(undefined, onErr)}><RefreshCw size={13} /></button>
          </span>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
          {pl.ALTIN_24 && <span>gram {tl(pl.ALTIN_24)}</span>}
          {pl.ALTIN_22 && <span>22 ayar {tl(pl.ALTIN_22)}</span>}
          {pl.CEYREK && <span>çeyrek {tl(pl.CEYREK)}</span>}
          {pl.USD && <span>$ {tl(pl.USD)}</span>}
          {pl.EUR && <span>€ {tl(pl.EUR)}</span>}
          <button className="underline decoration-dotted" onClick={() => {
            const v = window.prompt("Gram altın fiyatını elle gir (boş: piyasa fiyatı):", d.prices.manual_gram ? String(d.prices.manual_gram) : "");
            if (v != null) settings.mutate({ manual_gram: v.trim() ? parseAmount(v) : 0 }, onErr);
          }}>fiyatı elle gir</button>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-stone-100 pt-2 text-xs dark:border-stone-800">
          <label className="flex items-center gap-1">Mezhep
            <select className="input w-auto px-2 py-1 text-xs" value={d.settings.madhhab} onChange={(e) => settings.mutate({ madhhab: e.target.value }, onErr)}>
              <option value="hanefi">Hanefî (Diyanet)</option><option value="safii">Şâfiî</option>
            </select>
          </label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={d.settings.include_app}
            onChange={(e) => settings.mutate({ include_app: e.target.checked }, onErr)} /> Banka hesaplarım ve borçlarım dahil</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={d.settings.include_receivables}
            onChange={(e) => settings.mutate({ include_receivables: e.target.checked }, onErr)} /> Alacaklarım dahil</label>
        </div>
      </section>

      {d.people.map((p) => <PersonCard key={p.id} p={p} d={d} />)}

      <section className="card flex flex-wrap items-center gap-2 p-4">
        <UserPlus size={16} className="text-stone-500" />
        <input className="input min-w-0 flex-1" placeholder="Aile üyesi ekle (ör. Eşim, Annem)" value={newPerson} onChange={(e) => setNewPerson(e.target.value)} />
        <button className="btn-primary" disabled={!newPerson.trim()} onClick={() => addPerson.mutate(undefined, onErr)}>Ekle</button>
      </section>

      <section className="card p-4 text-sm">
        <button className="flex w-full items-center gap-2 text-left font-semibold" onClick={() => setShowRules(!showRules)}>
          <Info size={16} /> Nasıl hesaplanıyor?
          <ChevronDown size={14} className={cx("ml-auto text-stone-400 transition-transform", showRules && "rotate-180")} />
        </button>
        {showRules && (
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-xs text-stone-600 dark:text-stone-400">
            <li><b>Nisap:</b> 80,18 gram altın değeri. Altın, gümüş, TL, döviz ve alacaklar aynı havuzda toplanır.</li>
            <li><b>Yıl:</b> nisaba ulaştığın günden itibaren bir kameri yıl (354 gün). Nisabın yılın başında ve sonunda olması yeter; arada azalıp çoğalması önemli değil.</li>
            <li><b>Bozdurup yeniden almak:</b> yıl içinde altını paraya ya da dövize çevirip sonra yeniden alman yılı bozmaz; hepsi aynı havuz. Sadece zekâta tabi malın <b>tamamı biterse</b> yıl yeniden başlar ("Yıl başlangıcını değiştir").</li>
            <li><b>Tutar:</b> yıl dolduğu gündeki toplamın %2,5'i (kırkta biri). Hanefî'de yıl içinde eline geçen para ve altın da yıl sonundaki toplama dahildir; Şâfiî'de sonradan gelen her varlığın kendi yılı vardır, yılını doldurmayan o yıl sayılmaz.</li>
            <li><b>Kimin malı:</b> zekât sahibine düşer; ailede her kişi ayrı hesaplanır. Düğün takıları aksi konuşulmadıkça kadına aittir. Eşin zekâtını onun izniyle sen de verebilirsin.</li>
            <li><b>Ziynet:</b> Hanefî'ye (Diyanet) göre takılan altın da dahil; Şâfiî'ye göre mutad kullanılan ziynet muaf.</li>
            <li><b>Borçlar:</b> Hanefî'de vadesi bu yıl içinde dolan borçlar (kart ekstreleri, kısa vadeli borçlar) düşülür; uzun vadeli kredinin sadece bu yılki taksitleri. Şâfiî'de borç düşülmez.</li>
            <li><b>Dahil olmayanlar:</b> oturduğun ev, araba, eşyalar, yemek kartı bakiyesi.</li>
            <li><b>Ödeme:</b> erken ve parça parça verilebilir. Anne-baba, dede-nine, çocuk-torun ve eşe zekât verilmez.</li>
            <li>Kameri tarihler hesapla bulunur, resmi takvimden 1 gün sapabilir.</li>
            <li className="text-stone-500">Bilgilendirme amaçlıdır. Emin olmadığın durumda müftülüğe danış: <b>Alo Fetva 190</b>.</li>
          </ul>
        )}
      </section>

      <p className="text-center text-xs text-stone-500">
        Bu bilgiler yalnız senin hesabında. <button className="underline" onClick={() => setClosing(true)}>Zekâtı kapat ya da kayıtları sil</button>
      </p>
      {closing && <ZakatOff onClose={() => setClosing(false)} />}
    </div>
  );
}
