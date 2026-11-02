import { Check, Crown, Lock, Minus, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import type { PlanInfo, PlanTableRow, ReadKind, ReadStatus, Tier } from "../api";
import { cx, dateTR } from "../format";
import type { Route } from "../ui";
import { inApp } from "./AppServer";
import { useMeta } from "./hooks";

export const usePlan = () => useMeta().data?.plan;

export const TIERS: Tier[] = ["free", "premium", "aile"];
export const TIER_LABEL: Record<Tier, string> = { free: "Ücretsiz", premium: "Premium", aile: "Aile" };
const RANK: Record<Tier, number> = { free: 0, premium: 1, aile: 2 };
export const KIND_LABEL: Record<ReadKind, string> = { belge: "belge", fis: "fiş" };

/** Zekât her pakette ücretsiz: hiçbir koşulda kilitlenmez. */
const NEVER_LOCKED = new Set(["zekat"]);

/** Satırın açık olduğu en düşük paket: sunucunun tablosu (tiers / table); eski sunucu: ortak defter Aile, gerisi Premium. */
export function tierOf(p: PlanInfo | undefined, key: string): Tier {
  return p?.tiers?.[key] ?? p?.table?.find((r) => r.key === key)?.tier ?? (key === "ortak_defter" ? "aile" : "premium");
}

/** "Premium" / "Aile": kilitli bir özellik hangi pakette. */
export const needLabel = (p: PlanInfo | undefined, key: string) => TIER_LABEL[tierOf(p, key)];

export interface PlanLock { key: string; label: string; desc: string; need: string }

/** Paketinde kapalıysa kilit bilgisi (ad ve açıklama sunucu tablosundan), açıksa null. */
export function lockOf(p: PlanInfo | undefined, key: string): PlanLock | null {
  if (!p || NEVER_LOCKED.has(key) || p.features[key] !== false) return null;
  const row = p.table?.find((r) => r.key === key);
  return { key, label: row?.label ?? p.feature_labels[key] ?? key, desc: row?.desc ?? "", need: needLabel(p, key) };
}

export const useLock = (key: string | undefined) => {
  const p = usePlan();
  return key ? lockOf(p, key) : null;
};

/** Menüdeki sayfa → paket tablosundaki anahtar (sayfa satırları). İstatistik sayfası her pakette açık (karşılaştırma
 *  ve yıl özeti içeride kilitli), zekât hiç kilitlenmez. */
export const ROUTE_FEATURE: Partial<Record<Route, string>> = {
  plan: "planlama", hesaplar: "hesaplar", borclar: "borclar", transferler: "transferler", faturalar: "faturalar",
  tasarruf: "tasarruf", tablo: "tablo", kurallar: "kurallar", fiyatlar: "fiyatlar",
};

export function Upgrade({ need }: { need?: string }) {
  return (
    <p className="text-sm text-stone-600 dark:text-stone-400">
      {need ? <><b>{need}</b> paketinde var. </> : null}
      {inApp() ? "Abonelik yakında buradan alınabilecek." : "Abonelik HesapKitap telefon uygulamasından alınır (App Store / Google Play)."}
    </p>
  );
}

/** Menüde kilitli sayfanın yanındaki küçük rozet: gereken paket. */
export function LockBadge({ need, className }: { need: string; className?: string }) {
  return (
    <span className={cx("inline-flex shrink-0 items-center gap-0.5 rounded bg-amber-100 px-1.5 py-px text-[10px] font-semibold text-amber-800 dark:bg-amber-900/50 dark:text-amber-300", className)}>
      <Lock size={10} aria-hidden="true" /> {need}
    </span>
  );
}

/** Kilit açıklaması: ne olduğu, hangi pakette, abonelik nereden. */
export function LockCard({ lock, title, compact }: { lock: PlanLock; title?: string; compact?: boolean }) {
  if (compact) {
    return (
      <section className="card flex items-start gap-3 p-3 text-sm">
        <Lock size={18} className="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
        <div className="min-w-0 space-y-0.5">
          <h2 className="font-medium">{title ?? lock.label} <LockBadge need={lock.need} className="ml-1 align-middle" /></h2>
          {lock.desc && <p className="text-xs text-stone-500">{lock.desc}</p>}
          <Upgrade />
        </div>
      </section>
    );
  }
  return (
    <section className="card space-y-2 p-6 text-center">
      <Crown size={32} className="mx-auto text-amber-500" aria-hidden="true" />
      <h2 className="text-lg font-semibold">{title ?? lock.label}</h2>
      {lock.desc && <p className="text-sm text-stone-500">{lock.desc}</p>}
      <Upgrade need={lock.need} />
    </section>
  );
}

/** Paketinde olmayan bir özelliğin yerine: ne olduğunu ve hangi pakette olduğunu söyler.
 *  preview: içerik bulanık önizleme olarak altta görünür (yalnız sunucunun açık bıraktığı sayfa satırlarında;
 *  sunucunun da kilitlediği uçlarda içerik 402 alır, önizleme verilmez). compact: sayfa içindeki küçük bölüm. */
export function PlanGate({ feature, title, children, preview, compact }: {
  feature: string; title?: string; children: ReactNode; preview?: boolean; compact?: boolean;
}) {
  const lock = useLock(feature);
  if (!lock) return <>{children}</>;
  return (
    <div className="space-y-3">
      <LockCard lock={lock} title={title} compact={compact} />
      {preview && (
        <div aria-hidden="true" inert
          className="pointer-events-none max-h-96 select-none overflow-hidden opacity-60 blur-[3px] [mask-image:linear-gradient(to_bottom,black_40%,transparent)]">
          {children}
        </div>
      )}
    </div>
  );
}

/** Bu ayki sayaç: "Belge 3/10 · Fiş 12/30 · Ek 0". */
export function ReadCounts({ reads, className }: { reads: ReadStatus; className?: string }) {
  const item = (k: ReadKind) => {
    const c = reads[k];
    const out = c.left <= 0 && reads.extra <= 0;
    return (
      <span className={cx("whitespace-nowrap", out && "font-medium text-amber-800 dark:text-amber-300")}>
        {k === "belge" ? "Belge" : "Fiş"} <b className="tabular-nums">{c.used}/{c.limit}</b>
      </span>
    );
  };
  return (
    <span className={cx("inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5", className)}>
      {item("belge")}<span aria-hidden="true" className="text-stone-400">·</span>
      {item("fis")}<span aria-hidden="true" className="text-stone-400">·</span>
      <span className="whitespace-nowrap">Ek <b className="tabular-nums">{reads.extra}</b></span>
    </span>
  );
}

/** Ek okuma paketi notu: web'de satın alma yok. */
export function PackNote({ p, className }: { p: PlanInfo | undefined; className?: string }) {
  const packs = p?.packs?.map((x) => `+${x.reads}`).join(" / ");
  return (
    <p className={cx("text-xs text-stone-500", className)}>
      Ek okuma paketi{packs ? ` (${packs})` : ""} telefondaki uygulamadan alınır; belge ve fişte ortak, bitene kadar geçerli.
    </p>
  );
}

type Cell = ReactNode;
interface CompareRow { key: string; label: string; desc?: string; cells: [Cell, Cell, Cell] }

const yes = <Check size={16} className="mx-auto text-green-600 dark:text-green-500" aria-label="var" />;
const no = <Minus size={16} className="mx-auto text-stone-300 dark:text-stone-600" aria-label="yok" />;

/** Sunucu tablosu + okuma hakları → karşılaştırma satırları. Ücretsizde açık sayfa satırları tek satırda toplanır
 *  (yönetici bir sayfayı Premium'a alırsa kendi satırına çıkar). */
export function compareRows(p: PlanInfo): CompareRow[] {
  const rows: CompareRow[] = [];
  const reads = p.reads_by_plan;
  if (reads) {
    const n = (t: Tier, k: ReadKind) => (
      <span className="tabular-nums">{reads[t][k]}{t === "aile" && <span className="block text-[10px] text-stone-500">kişi başı</span>}</span>
    );
    rows.push({ key: "r-belge", label: "Belge okuma, ayda", desc: "Ekstre, hesap dökümü, fatura, posta belgesi, yapay zekâ kategori önerisi",
      cells: [n("free", "belge"), n("premium", "belge"), n("aile", "belge")] });
    rows.push({ key: "r-fis", label: "Fiş okuma, ayda", cells: [n("free", "fis"), n("premium", "fis"), n("aile", "fis")] });
  }
  if (p.packs?.length) {
    rows.push({ key: "r-pack", label: `Ek okuma paketi (${p.packs.map((x) => `+${x.reads}`).join(" / ")})`,
      desc: "Telefondaki uygulamadan; belge ve fişte ortak, bitene kadar geçerli", cells: [yes, yes, yes] });
  }
  const table: PlanTableRow[] = p.table ?? [];
  const byTier = (t: Tier): [Cell, Cell, Cell] => TIERS.map((c) => (RANK[c] >= RANK[t] ? yes : no)) as [Cell, Cell, Cell];
  const freePages: PlanTableRow[] = [];
  let zekat: PlanTableRow | undefined;
  for (const r of table) {
    if (r.key === "zekat") { zekat = r; continue; }
    if (r.kind === "sayfa" && r.tier === "free") { freePages.push(r); continue; }
    const desc = r.key === "ortak_defter" && p.family_seats ? r.desc || `${p.family_seats} kişiye kadar` : r.desc;
    rows.push({ key: r.key, label: r.label, desc: desc || undefined, cells: byTier(r.tier) });
  }
  if (freePages.length) {
    rows.push({ key: "r-pages", label: "Diğer sayfalar", desc: freePages.map((r) => r.label).join(", "), cells: [yes, yes, yes] });
  }
  if (zekat) rows.push({ key: "zekat", label: zekat.label, desc: "Her pakette ücretsiz", cells: [yes, yes, yes] });
  rows.push({ key: "r-export", label: "Verini indirmek, hesabını silmek", desc: "Her pakette serbest", cells: [yes, yes, yes] });
  return rows;
}

/** Ayarlar → Paketin (sadece barındırılan sürümde). */
export default function PlanSettings() {
  const p = usePlan();
  if (!p?.hosted) return null;
  const rows = compareRows(p);
  const prem = p.reads_by_plan?.premium;
  return (
    <section id="paket" className="card scroll-mt-4 space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><Sparkles size={18} aria-hidden="true" /> Paketin: {p.label}</h2>
        {p.until && <span className="text-xs text-stone-500">
          {p.trial ? `Deneme · ${p.trial_days_left != null ? `${p.trial_days_left} gün kaldı` : dateTR(p.until.slice(0, 10))}` : `${dateTR(p.until.slice(0, 10))} tarihine kadar`}
        </span>}
      </div>
      {p.member && <p className="text-sm text-stone-500">Ortak defterdesin: defter sahibinin paketini kullanırsın; okuma hakkın kendine ait.</p>}
      {p.reads && (
        <div className="rounded-lg bg-stone-50 p-2.5 text-sm dark:bg-stone-800/60">
          <span className="mr-1.5 text-stone-500">Bu ay:</span><ReadCounts reads={p.reads} />
        </div>
      )}
      <div className="-mx-1 overflow-x-auto px-1">
        <table className="w-full table-fixed text-sm">
          <caption className="sr-only">Paket karşılaştırması</caption>
          <colgroup><col /><col className="w-[4.5rem] sm:w-28" /><col className="w-[4.5rem] sm:w-28" /><col className="w-[4.5rem] sm:w-28" /></colgroup>
          <thead>
            <tr className="text-xs text-stone-500">
              <th scope="col" className="py-1 text-left font-normal"><span className="sr-only">Özellik</span></th>
              {TIERS.map((k) => (
                <th key={k} scope="col" className={cx("px-1 py-1 text-center font-medium", p.plan === k && "rounded-t-md bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-400")}>
                  {TIER_LABEL[k]}{p.plan === k && <span className="sr-only"> (senin paketin)</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
            {rows.map((r) => (
              <tr key={r.key}>
                <th scope="row" className="py-2 pr-2 text-left align-top font-normal">
                  {r.label}
                  {r.desc && <span className="block text-xs text-stone-500">{r.desc}</span>}
                </th>
                {r.cells.map((c, i) => (
                  <td key={i} className={cx("px-1 py-2 text-center align-top", p.plan === TIERS[i] && "bg-blue-50/60 dark:bg-blue-950/20")}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p.plan !== "aile" && <div className="space-y-1">
        {p.plan === "free" && prem && <p className="text-sm"><Crown size={14} className="mr-1 inline text-amber-500" aria-hidden="true" />
          <b>Premium:</b> ayda {prem.belge} belge ve {prem.fis} fiş okuma, geçmiş dönemler, fiyat takibi.</p>}
        {p.plan === "premium" && <p className="text-sm"><Crown size={14} className="mr-1 inline text-amber-500" aria-hidden="true" />
          <b>Aile:</b> {p.family_seats ?? 4} kişiye kadar herkes Premium'un tamamı, ortak defter ve ortak kumbara.</p>}
        <Upgrade />
      </div>}
      <PackNote p={p} />
      <p className="text-xs text-stone-500">Hak dolunca hiçbir şey kilitlenmez: yalnız yapay zekâ okuma durur, elle ekleme açık kalır.</p>
    </section>
  );
}
