// Fiyatlar → ürüne tıklayınca: anlaşılır özet ("₺138,00 → ₺129,00 (−%7) · 12 Eki → bugün"), iki alımdan itibaren altında
// grafik (her alım işaretli, ilk/son değer yazılı, yuvarlak eksen, yalnız alım olan aylar); tek alımda yalnız fiyat ve gün; "Alımların" listesi (fişe gider), tek market
// notu; sunucu "price_groups" verirse markası fark etmeyen grup ve "Nereden alınır" (kendi + topluluk fiyatı).
// Yetenek price_market: grafikte piyasa bandı (GET /api/prices/market: gün gün en düşük–en yüksek, kesikli ortanca;
// grafik o zaman takvim eksenli), alım noktası history[].vs_market'e göre yeşil / sarı / kırmızı, altında son alımın yeri.
import { useQuery } from "@tanstack/react-query";
import { ReceiptText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { marketCaption, marketSource, type Market, type VsMarket } from "../lib/optimize";
import { ayrilma, change7Text, changePct, dayWord, daysBetween, monthShort, niceTicks, officialSrc, pctWord, pricedPoints, type PricePoint } from "../lib/priceView";
import { useUI } from "../ui";
import { useMeta, useOpenRow } from "./hooks";
import { RegionPrompt } from "./RegionPicker";

export interface PriceHistory {
  date: string; price: number | null; store: string; branch: string | null; qty: number; name: string;
  /** yeni sunucu: kaydın uid'i ("m12") ve fiş kaydı; tahmini fiyat (fişte birim fiyat yoktu) */
  uid?: string | null; entry_id?: number | null; estimated?: boolean;
  /** yetenek price_market: o günün (±3 gün) piyasa fiyatına göre */
  vs_market?: VsMarket | null;
}
export interface PriceGroupRef { key: string; label: string; category?: string | null; pending?: boolean }
const VERDICT_BOX = {
  ucuz: "bg-green-50 text-green-800 dark:bg-green-950/50 dark:text-green-300",
  normal: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  pahali: "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300",
} as const;
const VERDICT_DOT = {
  ucuz: "fill-green-600 dark:fill-green-400",
  normal: "fill-amber-500 dark:fill-amber-400",
  pahali: "fill-red-600 dark:fill-red-400",
} as const;
export interface GroupBest {
  chain: string; price: number; brand?: string | null; source: "own" | "community" | "resmi"; date?: string | null; n?: number | null;
  /** yetenek price_unit: grubun boyundan litre / kilo / adet fiyatı */
  unit_price_per?: number | null; per_unit?: "L" | "kg" | "adet" | null;
  /** source "resmi": kaynak yazısı (gösterilmesi gerekir) */
  attribution?: string | null;
  /** yetenek price_region (resmi): kullanılan il ("İzmir") ya da "ulusal" (BİM, A101, ŞOK); indirim; 7 / 30 gün değişim (%) */
  region?: string | null; discount?: boolean; promotion?: string | null; change_7d?: number | null; change_30d?: number | null;
}
export interface PriceGroup { key: string; label: string; category?: string | null; best: GroupBest[] }

export const OFFICIAL_ATTRIBUTION = "Kaynak: Market Fiyatı (T.C. Sanayi ve Teknoloji Bakanlığı / TÜBİTAK BİLGEM)";
/** "₺59,00/L" (birim fiyat yoksa boş) */
export const perUnitText = (v?: number | null, u?: string | null) => (v != null && u ? `${tl(v)}/${u}` : "");
/** Resmî teklifin rozetleri: "İndirim" ve |7 gün| ≥ %3 ise "7 günde +%8" (zam kırmızı, düşüş yeşil). */
export function OfferBadges({ b }: { b: GroupBest }) {
  const ch = change7Text(b.change_7d);
  if (!b.discount && !ch) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {b.discount && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-950 dark:text-amber-300" title={b.promotion ?? undefined}>İndirim</span>}
      {ch && <span className={cx("rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums", (b.change_7d ?? 0) > 0 ? "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300" : "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-300")}>{ch}</span>}
    </span>
  );
}

/** Tekliflerde resmî kaynak varsa altına yazılacak kaynak satırı. */
export const attributionOf = (best: GroupBest[]) => {
  const r = best.find((b) => b.source === "resmi");
  return r ? r.attribution || OFFICIAL_ATTRIBUTION : null;
};
const srcWord = (b: GroupBest) => (b.source === "community" ? "topluluk" : b.source === "resmi" ? "Market Fiyatı" : "senin fişin");

const per = (u: string) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");
const qtyText = (q: number, unit: string) => `${q.toLocaleString("tr-TR", { maximumFractionDigits: 3 })} ${unit === "adet" ? "adet" : unit}`;

/** "BİM ₺34,50 (Torku)" + kaynak: "senin fişin, 3 gün" / "topluluk, 14 gün" / "Market Fiyatı, bugün, İzmir" */
export function bestLine(b: GroupBest, today: string) {
  const pu0 = perUnitText(b.unit_price_per, b.per_unit);
  if (b.source === "resmi") return { main: `${maskText(b.chain)} ${tl(b.price)}${pu0 ? ` · ${pu0}` : ""}${b.brand ? ` (${b.brand})` : ""}`, src: officialSrc(b, today) };
  const age = b.date ? daysBetween(b.date, today) : null;
  const ago = age == null ? "" : age <= 0 ? ", bugün" : `, ${age} gün`;
  const pu = perUnitText(b.unit_price_per, b.per_unit);
  return { main: `${maskText(b.chain)} ${tl(b.price)}${pu ? ` · ${pu}` : ""}${b.brand ? ` (${b.brand})` : ""}`, src: `${srcWord(b)}${ago}` };
}

export default function PriceDetail({ p }: { p: { product: string; unit: string; history: PriceHistory[]; group?: PriceGroupRef | null } }) {
  const ui = useUI();
  const openRow = useOpenRow();
  const caps = useMeta().data?.capabilities;
  const today = todayISO();
  const pts = pricedPoints(p.history);
  const stores = [...new Set(p.history.map((h) => h.store))];
  const group = caps?.includes("price_groups") ? p.group ?? null : null;
  const mkKey = caps?.includes("price_market") && p.group && !p.group.pending ? p.group.key : null;
  // bant geçmişi kapsasın: en az 90 gün, ilk alıma kadar (en çok 400)
  const mkDays = Math.min(400, Math.max(90, pts.length ? daysBetween(pts[0]!.date, today) + 3 : 90));
  const mk = useQuery({
    queryKey: ["price-market", mkKey, mkDays], enabled: !!mkKey, retry: false, staleTime: 10 * 60_000,
    queryFn: () => send<Market>(`/api/prices/market?group=${encodeURIComponent(mkKey!)}&days=${mkDays}`),
  });
  const band = mk.data?.days.length ? mk.data : null;
  const verdicts = p.history.map((h) => h.vs_market?.verdict ?? null);
  const caption = band ? marketCaption(p.history, (n) => maskText(tl(n))) : null;
  const rows = [...p.history].map((h, i) => ({ h, i })).sort((a, b) => b.h.date.localeCompare(a.h.date) || b.i - a.i);
  const open = (h: PriceHistory) => {
    const uid = h.uid ?? (h.entry_id != null ? `m${h.entry_id}` : null);
    if (uid) void openRow(uid, h.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)))
      .then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı (silinmiş olabilir)"); });
  };

  return (
    <div className="space-y-3 px-4 pb-4" data-testid="fiyat-detay">
      {pts.length > 0 && <Summary pts={pts} unit={p.unit} today={today} />}
      {(pts.length >= 2 || (pts.length === 1 && (band?.days.length ?? 0) >= 2)) && <Chart pts={pts} unit={p.unit} today={today} band={band} verdicts={verdicts} />}
      {caption && (
        <p data-testid="piyasa-yazi" className={cx("rounded-lg px-3 py-2 text-sm font-medium",
          VERDICT_BOX[[...p.history].filter((h) => h.price != null && h.vs_market).sort((a, b) => a.date.localeCompare(b.date)).pop()?.vs_market?.verdict ?? "normal"])}>{caption}</p>
      )}
      {stores.length === 1 && (
        <p className="rounded-lg bg-stone-50 px-3 py-2 text-xs text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
          Yalnız {ayrilma(maskText(stores[0]))} aldın; başka marketten alınca karşılaştırırız.</p>
      )}
      {group && <GroupCard g={group} today={today} />}
      <div>
        <h3 className="mb-1 text-xs font-semibold tracking-wide text-stone-500 uppercase">Alımların</h3>
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
          {rows.map(({ h, i }) => {
            const link = !!(h.uid || h.entry_id != null);
            return (
              <li key={i} className="flex items-center gap-3 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{dayWord(h.date, today)} · {maskText(h.store)}{h.branch ? <span className="font-normal text-stone-500"> ({h.branch})</span> : null}</div>
                  <div className="text-xs break-words text-stone-500">
                    {h.qty !== 1 && `${qtyText(h.qty, p.unit)} · `}<i>{h.name}</i>{h.estimated ? " · tahmini fiyat" : ""}</div>
                </div>
                <span className="shrink-0 text-right tabular-nums">{h.price != null ? <>{h.estimated ? "≈" : ""}{tl(h.price)}<span className="text-xs text-stone-500">{per(p.unit)}</span></> : "—"}</span>
                {link && (
                  <button type="button" className="btn-ghost shrink-0 px-2 py-1 text-xs" onClick={() => open(h)}
                    aria-label={`${dayWord(h.date, today)} ${h.store} fişini aç`}><ReceiptText size={14} aria-hidden /> Fiş</button>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/** Özet: tek alımda fiyat ve gün; iki ve üstünde ilk → son fiyat, değişim ve günler (altında grafik). */
function Summary({ pts, unit, today }: { pts: PricePoint[]; unit: string; today: string }) {
  const a = pts[0]!, b = pts[pts.length - 1]!;
  if (pts.length === 1) {
    return (
      <div className="rounded-lg bg-stone-50 p-3 dark:bg-stone-800/60" data-testid="fiyat-ozet">
        <p className="text-lg font-semibold tabular-nums">{tl(a.price)}<span className="text-xs font-normal text-stone-500">{per(unit)}</span></p>
        <p className="text-xs text-stone-500">{dayWord(a.date, today)} · {maskText(a.store)} · bir alım daha olunca değişimi gösteririz</p>
      </div>
    );
  }
  const c = changePct(a.price, b.price);
  return (
    <div className="rounded-lg bg-stone-50 p-3 dark:bg-stone-800/60" data-testid="fiyat-ozet">
      <p className="text-lg font-semibold tabular-nums">
        {tl(a.price)} → {tl(b.price)}{" "}
        <span className={cx("text-base", c > 0 ? "text-red-600 dark:text-red-400" : c < 0 ? "text-green-700 dark:text-green-400" : "text-stone-500")}>({pctWord(c)})</span>
      </p>
      <p className="text-xs text-stone-500">{dayWord(a.date, today)} → {dayWord(b.date, today)} · {pts.length} alım · {a.store === b.store ? maskText(a.store) : `${maskText(a.store)} → ${maskText(b.store)}`}</p>
    </div>
  );
}

/** ≥2 alım (özetin altında): her alım bir nokta, ilk ve son değer yazılı; tahmini fiyata giden parça kesikli. */
function Chart({ pts, unit, today, band, verdicts }: {
  pts: PricePoint[]; unit: string; today: string; band?: Market | null; verdicts?: ("ucuz" | "normal" | "pahali" | null)[];
}) {
  // çizim kutusu kabın gerçek genişliği: yazılar her ekranda aynı boyda (viewBox ölçeklenmez)
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(340);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => { if (e) setW(Math.max(240, Math.round(e.contentRect.width))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = W < 480 ? 180 : 210, L = 44, R = 14, T = 28, B = 24;
  // piyasa bandı varsa takvim ekseni (alımlar ve bant aynı günlere düşsün); yoksa alım sırası
  const bd = band?.days ?? [];
  const d0 = bd.length ? [pts[0]!.date, bd[0]!.day].sort()[0]! : "";
  const d1 = bd.length ? [pts[pts.length - 1]!.date, bd[bd.length - 1]!.day].sort()[1]! : "";
  const span = bd.length ? Math.max(1, daysBetween(d0, d1)) : 0;
  const xd = (day: string) => L + (daysBetween(d0, day) / span) * (W - L - R);
  const prices = [...pts.map((q) => q.price), ...bd.flatMap((d) => [d.min, d.max])];
  const ax = niceTicks(Math.min(...prices), Math.max(...prices));
  const x = (i: number) => (bd.length ? xd(pts[i]!.date) : pts.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (pts.length - 1));
  const y = (v: number) => T + (1 - (v - ax.lo) / (ax.hi - ax.lo || 1)) * (H - T - B);
  const anyEst = pts.some((x) => x.estimated);
  // ay etiketi: ayın ilk alımının altında (yalnız alım olan aylar)
  const months = pts.map((pt, i) => ({ i, ym: pt.date.slice(0, 7) })).filter((m, k, all) => k === 0 || all[k - 1]!.ym !== m.ym);
  const first = pts[0]!, last = pts[pts.length - 1]!;
  const c = changePct(first.price, last.price);
  const tick = (v: number) => `₺${v.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
  const hidden = tl(1).includes("•");
  return (
    <figure className="space-y-1" data-testid="fiyat-grafik">
      <figcaption className="sr-only">{pts.length} alım · {dayWord(first.date, today)} → {dayWord(last.date, today)} · {pctWord(c)}</figcaption>
      <div ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img"
        aria-label={`${pts.length} alımda fiyat ${tick(first.price)} liradan ${tick(last.price)} liraya`}>
        {ax.ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="stroke-stone-200 dark:stroke-stone-700" strokeWidth={1} />
            {!hidden && <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} className="fill-stone-500">{tick(v)}</text>}
          </g>
        ))}
        {bd.length > 1 && (
          <g data-testid="piyasa-bandi">
            <path d={`M${bd.map((d) => `${xd(d.day)},${y(d.max)}`).join("L")}L${[...bd].reverse().map((d) => `${xd(d.day)},${y(d.min)}`).join("L")}Z`}
              className="fill-stone-200/70 dark:fill-stone-700/50" />
            <polyline points={bd.map((d) => `${xd(d.day)},${y(d.median)}`).join(" ")} fill="none" strokeWidth={1.5} strokeDasharray="4 4"
              className="stroke-stone-400 dark:stroke-stone-500" />
          </g>
        )}
        {bd.length === 1 && <line x1={xd(bd[0]!.day) - 6} x2={xd(bd[0]!.day) + 6} y1={y(bd[0]!.median)} y2={y(bd[0]!.median)} strokeWidth={2} className="stroke-stone-400" />}
        {pts.slice(1).map((pt, k) => {
          const a = pts[k]!;
          return <line key={k} x1={x(k)} y1={y(a.price)} x2={x(k + 1)} y2={y(pt.price)} strokeWidth={2.5} strokeLinecap="round"
            strokeDasharray={a.estimated || pt.estimated ? "6 5" : undefined} className="stroke-blue-600 dark:stroke-blue-400" />;
        })}
        {pts.map((pt, i) => {
          const v = verdicts?.[pt.i] ?? null;
          return (
            <circle key={i} cx={x(i)} cy={y(pt.price)} r={v ? 5.5 : 4.5} strokeWidth={2} data-verdict={v ?? undefined}
              className={v ? cx(VERDICT_DOT[v], "stroke-white dark:stroke-stone-900") : cx("stroke-blue-600 dark:stroke-blue-400", pt.estimated ? "fill-white dark:fill-stone-900" : "fill-blue-600 dark:fill-blue-400")}>
              <title>{`${dayWord(pt.date, today)} · ${pt.store} · ${tl(pt.price)}${pt.estimated ? " (tahmini)" : ""}${v ? ` · ${v === "ucuz" ? "ucuz" : v === "pahali" ? "pahalı" : "normal"}` : ""}`}</title>
            </circle>
          );
        })}
        {!hidden && [0, pts.length - 1].map((i) => {
          const pt = pts[i]!;
          // değer noktanın üstünde (üstte T kadar boşluk var); uçta taşmasın diye içe doğru hizalı
          return <text key={i} x={x(i) + (i === 0 ? -4 : 4)} y={y(pt.price) - 10} textAnchor={i === 0 ? "start" : "end"} fontSize={12} fontWeight={600}
            className="fill-stone-800 dark:fill-stone-100">{tl(pt.price)}</text>;
        })}
        {months.map((m) => (
          <text key={m.ym} x={x(m.i)} y={H - 6} textAnchor={m.i === 0 ? "start" : m.i === pts.length - 1 ? "end" : "middle"} fontSize={11} className="fill-stone-500">{monthShort(m.ym)}</text>
        ))}
      </svg>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500">
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-full bg-blue-600 dark:bg-blue-400" aria-hidden /> Fişteki birim fiyat{per(unit) && ` (${per(unit).slice(1)})`}</span>
        {bd.length > 0 && <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2.5 w-4 rounded-sm bg-stone-200 dark:bg-stone-700" aria-hidden /> Piyasa (en düşük–en yüksek)</span>}
        {bd.length > 1 && <span className="inline-flex items-center gap-1.5"><svg width="22" height="6" aria-hidden><line x1="0" y1="3" x2="22" y2="3" strokeDasharray="4 4" strokeWidth="1.5" className="stroke-stone-400" /></svg> ortanca</span>}
        {anyEst && <span className="inline-flex items-center gap-1.5"><svg width="22" height="6" aria-hidden><line x1="0" y1="3" x2="22" y2="3" strokeDasharray="5 4" strokeWidth="2.5" className="stroke-blue-600 dark:stroke-blue-400" /></svg> Kesikli: tahmini fiyat</span>}
      </p>
      {band && <p className="text-[11px] text-stone-400 dark:text-stone-500" data-testid="piyasa-kaynak">{marketSource(band)}</p>}
    </figure>
  );
}

/** Aynı ürün (marka fark etmez) ve "Nereden alınır": GET /api/prices/groups?q=<grup>. */
function GroupCard({ g, today }: { g: PriceGroupRef; today: string }) {
  const q = useQuery({
    queryKey: ["price-groups", g.key], retry: false, staleTime: 5 * 60_000,
    queryFn: () => send<PriceGroup[] | { groups: PriceGroup[] }>(`/api/prices/groups?q=${encodeURIComponent(g.key)}`),
    select: (d) => (Array.isArray(d) ? d : d.groups ?? []),
  });
  const grp = q.data?.find((x) => x.key === g.key) ?? q.data?.[0];
  const best = [...(grp?.best ?? [])].sort((a, b) => a.price - b.price).slice(0, 5);
  return (
    <section className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700" data-testid="nereden-alinir">
      <p className="text-xs text-stone-500">Aynı ürün (marka fark etmez): <b className="text-stone-800 dark:text-stone-100">{g.label}</b></p>
      <h3 className="text-sm font-semibold">Nereden alınır</h3>
      {q.isLoading && <p className="text-xs text-stone-500">Yükleniyor…</p>}
      {q.error && <p className="text-xs text-stone-500">Şu an karşılaştırılamadı.</p>}
      {grp && !best.length && <p className="text-xs text-stone-500">Bu ürün için henüz başka fiyat yok.</p>}
      {best.length > 0 && (
        <ul className="space-y-1 text-sm">
          {best.map((b, i) => {
            const l = bestLine(b, today);
            return (
              <li key={`${b.chain}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5" data-testid="nereden-satir">
                <span className={cx("tabular-nums", i === 0 && "font-semibold text-green-700 dark:text-green-400")}>{l.main}{i === 0 && best.length > 1 ? " · en ucuz" : ""} <OfferBadges b={b} /></span>
                <span className="text-xs text-stone-500">{l.src}{b.source === "community" && b.n ? ` · ${b.n} kişi` : ""}</span>
              </li>
            );
          })}
        </ul>
      )}
      <RegionPrompt show={best.some((b) => b.source === "resmi")} />
      {attributionOf(best) && <p className="text-[11px] text-stone-500" data-testid="resmi-kaynak">{attributionOf(best)}</p>}
    </section>
  );
}
