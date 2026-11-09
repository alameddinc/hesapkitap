import { useQuery } from "@tanstack/react-query";
import { Activity, Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { call } from "../lib/apiError";
import { cx } from "../format";
import Segmented from "./Segmented";
import SorguDurumu, { KartIskelet } from "./SorguDurumu";

// Yönetici → Ölçüm (sunucu app/funnel.py, GET /api/admin/funnel): demo → hesap → aktivasyon → tutunma → mağaza →
// paket ekranı. Yalnız toplamlar; kişi listesi yok. Platform süzgeci bütün bölümlere uygulanır.

type Plat = "hepsi" | "web" | "ios" | "android";
interface Cohort {
  signups: number; web: number; ios: number; android: number; mobil: number; bilinmiyor: number;
  act_n: number; act_doc: number; act_rec: number; act_any: number; act_any_pct: number | null; act_doc_pct: number | null; act_rec_pct: number | null;
  w2_n: number; w2: number; w2_pct: number | null; w4_n: number; w4: number; w4_pct: number | null;
}
type Paywall = Record<"paywall_view" | "plans_open" | "purchase_start" | "purchase_success" | "purchase_cancel", { users: number; n: number }>;
type Store = Record<string, number>;
interface Week { start: string; end: string; partial: boolean; cohort: Cohort; signup_demo: number; demo: { sessions: number; visitors: number } | null; paywall: Paywall; store: Store }
export interface Funnel {
  platform: string; today: string; started: string | null; enabled: boolean; demo_configured: boolean; demo_error: string | null;
  demo_available: boolean; store_sandbox: number; weeks: Week[];
  /** son N haftanın toplamı (sunucu hesaplar) */
  window: { signups: number; web: number; ios: number; android: number; other: number; signup_demo: number; demo_sessions: number | null };
  total: { cohort: Cohort; signup_demo: number; paywall: Paywall; store: Store; store_split: Record<string, Record<string, number>>;
    paywall_by_state: Record<string, Record<string, number>> };
  subs: { active: Record<string, number>; unknown_product: number; trials: Record<string, number>; ending: number; paying: number;
    mrr_gross: number; mrr_net: number; prices: Record<string, number> };
  definitions: Record<string, string>;
}

const n = (x: number | null | undefined) => (x ?? 0).toLocaleString("tr-TR");
const pct = (x: number | null | undefined) => (x == null ? "—" : `%${x.toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`);
const tl = (x: number) => `₺${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const wk = (w: Week) => `${w.start.slice(8, 10)}.${w.start.slice(5, 7)}`;
const ratio = (k: number, d: number) => (d ? Math.round((1000 * k) / d) / 10 : null);

/** Haftalık küçük çubuklar (eskiden yeniye); son (eksik) hafta soluk. */
export function Spark({ values, partialLast, label }: { values: number[]; partialLast?: boolean; label: string }) {
  const max = Math.max(1, ...values);
  return (
    <div className="flex h-8 min-w-0 shrink items-end gap-px sm:gap-0.5" role="img" aria-label={`${label}: ${values.join(", ")}`}>
      {values.map((v, i) => (
        <span key={i} className={cx("w-1 rounded-sm bg-blue-500 sm:w-1.5 dark:bg-blue-400", partialLast && i === values.length - 1 && "opacity-40")}
          style={{ height: `${Math.max(v ? 12 : 4, (v / max) * 100)}%`, ...(v ? {} : { opacity: 0.25 }) }} />
      ))}
    </div>
  );
}

function Tile({ label, value, note, spark }: { label: string; value: string; note?: string; spark?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-stone-50 p-2 dark:bg-stone-800/50">
      <div className="text-xs leading-tight text-stone-500">{label}</div>
      <div className="flex items-end justify-between gap-2">
        <div className="whitespace-nowrap font-semibold tabular-nums">{value}</div>
        {spark}
      </div>
      {note && <div className="text-[11px] leading-tight text-stone-500 tabular-nums">{note}</div>}
    </div>
  );
}

/** Haftalık tablo: ilk sütun hafta (yapışkan), en yeni hafta üstte, son satır toplam. Dar ekranda kart içinde kayar. */
function Table({ head, rows, total, caption }: { head: string[]; rows: { key: string; partial?: boolean; cells: ReactNode[] }[];
  total?: ReactNode[]; caption: string }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-max whitespace-nowrap text-sm tabular-nums">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="text-left text-xs text-stone-500">
            {head.map((h, i) => <th key={h} scope="col" className={cx("pb-1 font-normal", i === 0 ? "sticky left-0 bg-white pr-2 dark:bg-stone-900" : "px-1 text-right")}>{h}</th>)}
          </tr>
        </thead>
        <tbody className="[&_td]:border-t [&_th]:border-t [&_td]:border-stone-100 [&_th]:border-stone-100 dark:[&_td]:border-stone-800 dark:[&_th]:border-stone-800">
          {rows.map((r) => (
            <tr key={r.key} className={cx(r.partial && "text-stone-500")}>
              {r.cells.map((c, i) => i === 0
                ? <th key={i} scope="row" className="sticky left-0 bg-white py-1 pr-2 text-left font-normal dark:bg-stone-900">{c}</th>
                : <td key={i} className="px-1 py-1 text-right">{c}</td>)}
            </tr>
          ))}
        </tbody>
        {total && (
          <tfoot>
            <tr className="border-t border-stone-200 font-semibold dark:border-stone-700">
              {total.map((c, i) => i === 0
                ? <th key={i} scope="row" className="sticky left-0 bg-white py-1 pr-2 text-left dark:bg-stone-900">{c}</th>
                : <td key={i} className="px-1 py-1 text-right">{c}</td>)}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** "3/8 · %37,5": pay / payda ve oran (payda yoksa —). */
const frac = (k: number, d: number) => (d ? <span>{n(k)}<span className="text-stone-400">/{n(d)}</span> <span className="text-xs text-stone-500">{pct(ratio(k, d))}</span></span> : "—");

function Section({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="card min-w-0 space-y-3 p-4">
      <h3 className="font-semibold">{title}</h3>
      {children}
      {note && <p className="text-xs text-stone-500">{note}</p>}
    </section>
  );
}

const STEPS: [keyof Paywall, string][] = [["paywall_view", "Kilitli sayfa"], ["plans_open", "Paket ekranı"],
  ["purchase_start", "Satın alma başladı"], ["purchase_success", "Başarılı"], ["purchase_cancel", "Vazgeçti"]];
const STORE_COLS: [string, string][] = [["trial_start", "Deneme"], ["purchase", "Doğrudan"], ["trial_convert", "Denemeden ücretliye"],
  ["renewal", "Yenileme"], ["trial_cancel", "Denemede iptal"], ["cancel", "İptal"], ["refund", "İade"], ["expire", "Bitti"], ["pack", "Ek paket"]];
const PROD: Record<string, string> = { premium_monthly: "Premium aylık", premium_yearly: "Premium yıllık", aile_monthly: "Aile aylık", aile_yearly: "Aile yıllık" };

export function OlcumView({ d }: { d: Funnel }) {
  const weeks = [...d.weeks].reverse(); // tablo: en yeni üstte
  const t = d.total;
  const lastPartial = d.weeks.at(-1)?.partial;
  const spark = (f: (w: Week) => number, label: string) => <Spark values={d.weeks.map(f)} partialLast={lastPartial} label={label} />;
  const win = d.window;
  const pw = t.paywall;
  return (
    <div className="space-y-4">
      {!d.enabled && <p className="card p-3 text-sm text-amber-800 dark:text-amber-300">Ölçüm bu sunucuda kapalı (yalnız hazır sürümde açık; kendi sunucunda HESAPKITAP_FUNNEL=1).</p>}
      <div className="card grid grid-cols-2 gap-2 p-3 sm:grid-cols-4">
        <Tile label={`Yeni hesap (${d.weeks.length} hafta)`} value={n(win.signups)} note={`toplam ${n(t.cohort.signups)}`} spark={spark((w) => w.cohort.signups, "Haftalık yeni hesap")} />
        <Tile label="Aktivasyon (ilk 7 gün)" value={pct(t.cohort.act_any_pct)} note={`${n(t.cohort.act_any)} / ${n(t.cohort.act_n)} hesap`} />
        <Tile label="2. hafta geri gelen" value={pct(t.cohort.w2_pct)} note={`${n(t.cohort.w2)} / ${n(t.cohort.w2_n)}`} />
        <Tile label="4. hafta geri gelen" value={pct(t.cohort.w4_pct)} note={`${n(t.cohort.w4)} / ${n(t.cohort.w4_n)}`} />
        <Tile label="Ödeyen abone" value={n(d.subs.paying)} note={`${n((d.subs.trials.premium ?? 0) + (d.subs.trials.aile ?? 0))} denemede · ${n(d.subs.ending)} iptal etti`} />
        <Tile label="Aylık gelir (brüt)" value={tl(d.subs.mrr_gross)} note={`net ≈ ${tl(d.subs.mrr_net)}`} />
        <Tile label="Deneme → ücretli" value={pct(ratio(t.store.trial_convert ?? 0, t.store.trial_start ?? 0))}
          note={`${n(t.store.trial_convert)} / ${n(t.store.trial_start)} deneme`} spark={spark((w) => w.store.trial_start ?? 0, "Haftalık deneme başlatma")} />
        <Tile label="Paket ekranından satın alma" value={pct(ratio(pw.purchase_success.users, pw.plans_open.users))}
          note={`${n(pw.purchase_success.users)} / ${n(pw.plans_open.users)} kişi-gün`} />
      </div>

      <Section title="Demo → hesap" note={<>
        {d.demo_error ? `Demo sunucusuna ulaşılamadı: ${d.demo_error}. ` : !d.demo_configured ? "Demo istatistiği bağlı değil (DEMO_STATS_URL). " :
          !d.demo_available ? "Demo sunucusu bu platform ayrımını henüz vermiyor (demo güncellenince gelir). " : ""}
        "Demodan" = hesap açan kişinin uygulaması önce demoyu denediğini bir kez bildirdi (web'de demo bağlantısı ?k=demo ile gelenler).
        Kimlik eşleştirmesi yok: demo oturumu ve hesap ayrı sayılır, oran yaklaşıktır.</>}>
        <Table caption="Haftalık demo oturumu ve yeni hesap" head={["Hafta", "Demo oturumu", "Yeni hesap", "Demodan", "Web", "iOS", "Android", "Bilinmiyor"]}
          rows={weeks.map((w) => ({ key: w.start, partial: w.partial, cells: [wk(w) + (w.partial ? " *" : ""),
            w.demo ? n(w.demo.sessions) : "—", n(w.cohort.signups), n(w.signup_demo), n(w.cohort.web), n(w.cohort.ios), n(w.cohort.android),
            n(w.cohort.bilinmiyor + w.cohort.mobil)] }))}
          total={[`${d.weeks.length} hafta`, win.demo_sessions == null ? "—" : n(win.demo_sessions), n(win.signups), n(win.signup_demo),
            n(win.web), n(win.ios), n(win.android), n(win.other)]} />
      </Section>

      <Section title="Aktivasyon ve tutunma (hesap açılan haftaya göre)" note={<>
        Pencere dolmamış hesap paydaya girmez (—). Ölçüm {d.started ? `${d.started.split("-").reverse().join(".")} tarihinde` : "henüz"} başladı;
        öncesinde açılan hesaplar oranlara girmez.</>}>
        <Table caption="Kohort: aktivasyon, 2. ve 4. hafta" head={["Hafta", "Hesap", "Aktivasyon", "≥1 belge", "≥5 kayıt", "2. hafta", "4. hafta"]}
          rows={weeks.map((w) => ({ key: w.start, partial: w.partial, cells: [wk(w) + (w.partial ? " *" : ""), n(w.cohort.signups),
            frac(w.cohort.act_any, w.cohort.act_n), frac(w.cohort.act_doc, w.cohort.act_n), frac(w.cohort.act_rec, w.cohort.act_n),
            frac(w.cohort.w2, w.cohort.w2_n), frac(w.cohort.w4, w.cohort.w4_n)] }))}
          total={["Toplam", n(t.cohort.signups), frac(t.cohort.act_any, t.cohort.act_n), frac(t.cohort.act_doc, t.cohort.act_n),
            frac(t.cohort.act_rec, t.cohort.act_n), frac(t.cohort.w2, t.cohort.w2_n), frac(t.cohort.w4, t.cohort.w4_n)]} />
      </Section>

      <Section title="Mağaza: deneme, satın alma, iptal" note={<>
        RevenueCat olayları. Toplam sütunu tüm zamanlar.{d.store_sandbox ? ` Test (sandbox) olayı: ${n(d.store_sandbox)} — sayılara girmedi.` : ""}
        {d.platform === "web" ? " Web'de mağaza satın alımı yok." : ""}</>}>
        <Table caption="Haftalık mağaza olayları" head={["Hafta", ...STORE_COLS.map(([, l]) => l)]}
          rows={weeks.map((w) => ({ key: w.start, partial: w.partial, cells: [wk(w) + (w.partial ? " *" : ""), ...STORE_COLS.map(([k]) => n(w.store[k]))] }))}
          total={["Toplam", ...STORE_COLS.map(([k]) => n(t.store[k]))]} />
        <div className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <div className="mb-1 text-xs text-stone-500">Şu an ücretli abonelik (deneme hariç)</div>
            <ul className="space-y-0.5 tabular-nums">
              {Object.entries(d.subs.active).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-2"><span>{PROD[k] ?? k}</span>
                  <span>{n(v)} <span className="text-xs text-stone-500">× {tl(d.subs.prices[k] ?? 0)}</span></span></li>))}
              {!!d.subs.unknown_product && <li className="flex justify-between"><span>Ürünü bilinmeyen</span><span>{n(d.subs.unknown_product)}</span></li>}
            </ul>
          </div>
          <div>
            <div className="mb-1 text-xs text-stone-500">Toplamda paket ve dönem</div>
            <ul className="space-y-0.5 tabular-nums">
              {(["trial_start", "purchase", "trial_convert"] as const).map((k) => (
                <li key={k}><span className="font-medium">{STORE_COLS.find((c) => c[0] === k)?.[1]}</span>
                  <span className="block text-stone-600 dark:text-stone-300">{Object.entries(t.store_split[k] ?? {}).map(([p, v]) =>
                    `${p.replace("premium", "Premium").replace("aile", "Aile").replace("·monthly", " aylık").replace("·yearly", " yıllık")} ${n(v)}`).join(" · ") || "—"}</span></li>))}
            </ul>
          </div>
        </div>
      </Section>

      <Section title="Paket ekranı hunisi" note="Kişi-gün: aynı kişi aynı gün bir kez sayılır. Web'de satın alma yok; yalnız kilitli sayfa görüntülenir.">
        <ol className="space-y-1.5 text-sm">
          {STEPS.map(([k, l], i) => {
            const v = pw[k].users;
            const top = Math.max(1, pw.paywall_view.users, pw.plans_open.users);
            const prev = i > 0 && i < 4 ? pw[STEPS[i - 1][0]].users : null;
            return (
              <li key={k} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-2">
                <span className="truncate">{l}</span>
                <span className="h-2.5 rounded bg-stone-100 dark:bg-stone-800"><span className={cx("block h-full rounded", k === "purchase_cancel" ? "bg-amber-500" : "bg-blue-500")} style={{ width: `${(v / top) * 100}%` }} /></span>
                <span className="w-24 text-right tabular-nums">{n(v)}{prev != null && <span className="text-xs text-stone-500"> {pct(ratio(v, prev))}</span>}</span>
              </li>
            );
          })}
        </ol>
        <Table caption="Haftalık paket ekranı olayları (kişi-gün)" head={["Hafta", ...STEPS.map(([, l]) => l)]}
          rows={weeks.map((w) => ({ key: w.start, partial: w.partial, cells: [wk(w) + (w.partial ? " *" : ""), ...STEPS.map(([k]) => n(w.paywall[k].users))] }))}
          total={["Toplam", ...STEPS.map(([k]) => n(pw[k].users))]} />
      </Section>

      <details className="card p-4 text-sm">
        <summary className="flex cursor-pointer items-center gap-2 font-medium"><Info size={16} aria-hidden /> Tanımlar ve gizlilik</summary>
        <dl className="mt-2 space-y-2">
          {Object.entries(d.definitions).map(([k, v]) => (
            <div key={k}><dt className="font-medium capitalize">{k}</dt><dd className="text-stone-600 dark:text-stone-400">{v}</dd></div>))}
          <div><dt className="font-medium">Gizlilik</dt><dd className="text-stone-600 dark:text-stone-400">Üçüncü taraf analitik yok, IP ve cihaz
            kimliği saklanmaz. Kişiye bağlı tek kayıt "hangi gün kullandı" satırıdır (400 gün); olaylar ve mağaza yalnız günlük toplamdır.</dd></div>
        </dl>
      </details>
      <p className="text-xs text-stone-500">* Bu hafta henüz bitmedi. Bugün: {d.today.split("-").reverse().join(".")}.</p>
    </div>
  );
}

/** Yönetici paneli → Ölçüm. */
export default function Olcum() {
  const [plat, setPlat] = useState<Plat>("hepsi");
  const q = useQuery({ queryKey: ["admin-funnel", plat], staleTime: 60_000,
    queryFn: () => call<Funnel>("GET", `/api/admin/funnel${plat === "hepsi" ? "" : `?platform=${plat}`}`) });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-lg font-semibold"><Activity size={20} aria-hidden /> Ölçüm</h2>
        <div className="w-full sm:w-80"><Segmented label="Platform" value={plat} onChange={setPlat}
          options={[{ value: "hepsi", label: "Hepsi" }, { value: "web", label: "Web" }, { value: "ios", label: "iOS" }, { value: "android", label: "Android" }]} /></div>
      </div>
      {q.data ? <OlcumView d={q.data} /> : <SorguDurumu q={q} etiket="Ölçüm yükleniyor"><KartIskelet satir={4} /></SorguDurumu>}
    </div>
  );
}
