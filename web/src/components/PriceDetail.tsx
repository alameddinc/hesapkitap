// Fiyatlar → ürüne tıklayınca: anlaşılır özet ("₺138,00 → ₺129,00 (−%7) · 12 Eki → bugün"), iki alımdan itibaren altında
// grafik (her alım işaretli, ilk/son değer yazılı, yuvarlak eksen, yalnız alım olan aylar); tek alımda yalnız fiyat ve gün; "Alımların" listesi (fişe gider), tek market
// notu; sunucu "price_groups" verirse markası fark etmeyen grup ve "Nereden alınır" (kendi + topluluk fiyatı).
// Yetenek price_market: grafikte piyasa bandı (GET /api/prices/market: gün gün en düşük–en yüksek, kesikli ortanca;
// grafik o zaman takvim eksenli), alım noktası history[].vs_market'e göre yeşil / sarı / kırmızı, altında son alımın yeri.
// Liste v4: "Nereden alınır"da gizli mağaza ipucu (shop_store_filter; hidden_count) ve marka + boy seçimiyle "Mağazalara
// göre" listesi (shop_brand_size; GET /api/prices/brands + /api/prices/offers, components/ShopOffers.tsx).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ListPlus, ReceiptText, Store, TrendingDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { marketCaption, type Market, type VsMarket } from "../lib/optimize";
import { adviceText, communityParts, type Advice, type MarketRef } from "../lib/priceAdvice";
import { agoWord, ayrilma, bulunma, change7Text, changePct, dayWord, daysBetween, monthShort, niceTicks, pctWord, pricedPoints, storeSeries, type PricePoint } from "../lib/priceView";
import { chainName, priceFootnote } from "../lib/shopList";
import { useUI } from "../ui";
import { keySizeLabel, sizeOptions } from "../lib/shopStores";
import { useCap, useMeta, useOpenRow } from "./hooks";
import { RegionPrompt } from "./RegionPicker";
import { BrandChips, OffersList, type BrandOpt } from "./ShopOffers";
import { HiddenHint } from "./ShopStores";

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
export interface GroupBest {
  chain: string; price: number; brand?: string | null; source: "own" | "community" | "resmi"; date?: string | null; n?: number | null;
  /** yetenek price_unit: grubun boyundan litre / kilo / adet fiyatı */
  unit_price_per?: number | null; per_unit?: "L" | "kg" | "adet" | null;
  /** source "resmi": kaynak yazısı (gösterilmesi gerekir) */
  attribution?: string | null;
  /** yetenek price_region (resmi): kullanılan il ("İzmir") ya da "ulusal" (BİM, A101, ŞOK); indirim; 7 / 30 gün değişim (%) */
  region?: string | null; discount?: boolean; promotion?: string | null; change_7d?: number | null; change_30d?: number | null;
  /** yetenek community_quality: aynı zincirde Market Fiyatı, fark >%10, indirimli satırların ortancası */
  official?: { price: number; date?: string | null; region?: string | null } | null; differs?: boolean; discounted_price?: number | null;
}
export interface PriceGroup { key: string; label: string; category?: string | null; best: GroupBest[]; /** shop_store_filter */ hidden_count?: number }

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

/** Tekliflerin en yeni resmî günü (satırda yalnız bundan eski teklifin yaşı yazılır). */
export const newestOfficial = (best: GroupBest[]) => best.filter((b) => b.source === "resmi").map((b) => b.date ?? "").filter(Boolean).sort().pop() ?? null;

/** Tek dipnot: "Fiyatlar: Market Fiyatı · dün"; yasal kaynak yazısı ipucunda (title) ve ekran okuyucuda. */
export function PriceFootnote({ best, today, className }: { best: GroupBest[]; today: string; className?: string }) {
  const t = priceFootnote(best, today);
  const a = attributionOf(best);
  if (!t) return null;
  const inText = !!a && t.startsWith(a); // sunucunun yazısı dipnotta: ipucunda yinelenmez
  return <p className={cx("text-xs text-stone-500 dark:text-stone-400", className)} title={inText ? undefined : a ?? undefined} data-testid="resmi-kaynak">
    {t}{a && !inText && <span className="sr-only"> ({a})</span>}</p>;
}

/** Tekliflerde resmî kaynak varsa altına yazılacak kaynak satırı. */
export const attributionOf = (best: GroupBest[]) => {
  const r = best.find((b) => b.source === "resmi");
  return r ? r.attribution || OFFICIAL_ATTRIBUTION : null;
};
const srcWord = (b: GroupBest) => (b.source === "community" ? "topluluk" : b.source === "resmi" ? "Market Fiyatı" : "senin fişin");

const per = (u: string) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");
const qtyText = (q: number, unit: string) => `${q.toLocaleString("tr-TR", { maximumFractionDigits: 3 })} ${unit === "adet" ? "adet" : unit}`;

/** "BİM ₺34,50 · ₺34,50/L" + kaynak: "senin fişin, 3 gün" / "topluluk, 14 gün"; resmî teklifte kaynak tek dipnotta
 *  ("Fiyatlar: Market Fiyatı · dün"), satırda yalnız dipnottan eskiyse yaşı. Yalnız zincir adı: marka, il, şube yok. */
export function bestLine(b: GroupBest, today: string, newest?: string | null) {
  const pu0 = perUnitText(b.unit_price_per, b.per_unit);
  if (b.source === "resmi") return { main: `${maskText(chainName(b.chain))} ${tl(b.price)}${pu0 ? ` · ${pu0}` : ""}`, src: b.date && newest && b.date < newest ? agoWord(b.date, today) : "" };
  const age = b.date ? daysBetween(b.date, today) : null;
  const ago = age == null ? "" : age <= 0 ? ", bugün" : `, ${age} gün`;
  const pu = perUnitText(b.unit_price_per, b.per_unit);
  return { main: `${maskText(chainName(b.chain))} ${tl(b.price)}${pu ? ` · ${pu}` : ""}`, src: `${srcWord(b)}${ago}` };
}

export interface PriceDetailProduct {
  product: string; unit: string; history: PriceHistory[]; group?: PriceGroupRef | null;
  /** yetenek price_advice: birleştirilen yazılışlar, piyasa karşılaştırması gösterilsin mi, son piyasa referansı, öneri */
  aliases?: string[] | null; market?: boolean | null; market_ref?: MarketRef | null; advice?: Advice | null;
}

export default function PriceDetail({ p, onChain }: { p: PriceDetailProduct; onChain?: (chain: string) => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const openRow = useOpenRow();
  const caps = useMeta().data?.capabilities;
  const today = todayISO();
  const pts = pricedPoints(p.history);
  const stores = [...new Set(p.history.map((h) => h.store))];
  const adviceOn = !!caps?.includes("price_advice");
  const isMarket = adviceOn ? p.market !== false : true;
  const group = caps?.includes("price_groups") && isMarket ? p.group ?? null : null;
  // eski sunucu (price_advice yok): piyasa referansı gün gün banttan (son gün)
  const mkKey = !adviceOn && caps?.includes("price_market") && p.group && !p.group.pending ? p.group.key : null;
  const mkDays = Math.min(400, Math.max(90, pts.length ? daysBetween(pts[0]!.date, today) + 3 : 90));
  const mk = useQuery({
    queryKey: ["price-market", mkKey, mkDays], enabled: !!mkKey, retry: false, staleTime: 10 * 60_000,
    queryFn: () => send<Market>(`/api/prices/market?group=${encodeURIComponent(mkKey!)}&days=${mkDays}`),
  });
  const lastDay = mk.data?.days[mk.data.days.length - 1];
  const ref: MarketRef | null = !isMarket ? null : adviceOn ? p.market_ref ?? null
    : lastDay ? { median: lastDay.median, min: lastDay.min, cheapest_chain: lastDay.cheapest_chain, day: lastDay.day, source: lastDay.source } : null;
  const money = (n: number) => maskText(tl(n));
  const advice = adviceOn ? adviceText(p.advice, money) : null;
  const caption = !advice && isMarket && ref ? marketCaption(p.history, money) : null;
  const rows = [...p.history].map((h, i) => ({ h, i })).sort((a, b) => b.h.date.localeCompare(a.h.date) || b.i - a.i);
  const open = (h: PriceHistory) => {
    const uid = h.uid ?? (h.entry_id != null ? `m${h.entry_id}` : null);
    if (uid) void openRow(uid, h.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)))
      .then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı (silinmiş olabilir)"); });
  };
  const [added, setAdded] = useState(false);
  const addToList = useMutation({
    // önerinin miktarı (ör. 6 süt) listeye de o kadar gider
    mutationFn: () => send("/api/shopping-list", { method: "POST", json: { text: p.product,
      qty: p.advice?.kind === "cheaper_at" && p.advice.qty > 1 ? p.advice.qty : 1, ...(p.group && !p.group.pending && { group_key: p.group.key }) } }),
    onSuccess: () => { setAdded(true); ui.toast("Alışveriş listesine eklendi", { action: { label: "Listeyi aç", run: () => ui.go("alisveris") } }); void qc.invalidateQueries({ queryKey: ["shopping-list"] }); void qc.invalidateQueries({ queryKey: ["shopping-plan"] }); },
    onError: (e: Error) => ui.error(e.message),
  });
  const adv = p.advice;

  return (
    <div className="space-y-3 px-4 pb-4" data-testid="fiyat-detay">
      {advice && adv && (
        <section data-testid="onerimiz" data-kind={adv.kind} className={cx("space-y-2 rounded-xl border p-3.5",
          advice.tone === "save" ? "border-green-200 bg-green-50/70 dark:border-green-900 dark:bg-green-950/30"
            : advice.tone === "good" ? "border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900" : "border-blue-200 bg-blue-50/60 dark:border-blue-900 dark:bg-blue-950/30")}>
          <p className="text-[11px] font-semibold tracking-wide text-stone-500 uppercase dark:text-stone-400">Önerimiz</p>
          <div className="flex items-start gap-2.5">
            {advice.tone === "good" && <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-green-600 text-white dark:bg-green-500 dark:text-stone-900"><Check size={15} strokeWidth={3} aria-hidden /></span>}
            {advice.tone === "save" && <TrendingDown size={20} className="mt-0.5 shrink-0 text-green-700 dark:text-green-400" aria-hidden />}
            {advice.tone === "info" && <Store size={20} className="mt-0.5 shrink-0 text-blue-700 dark:text-blue-300" aria-hidden />}
            <div className="min-w-0">
              <p className="font-semibold break-words tabular-nums" data-testid="onerimiz-baslik">{advice.title}</p>
              <p className="text-sm break-words text-stone-600 tabular-nums dark:text-stone-300">{advice.detail}</p>
            </div>
          </div>
          {adv.kind === "cheaper_at" && (
            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              {caps?.includes("shopping_list") && (
                <button type="button" className="btn-primary justify-center" disabled={addToList.isPending || added} onClick={() => addToList.mutate()}>
                  {added ? <><Check size={15} aria-hidden /> Listede</> : <><ListPlus size={15} aria-hidden /> Alışveriş listesine ekle</>}</button>
              )}
              {onChain && <button type="button" className="btn-ghost text-sm" onClick={() => onChain(adv.chain)}>{bulunma(adv.chain)} başka ne ucuz?</button>}
            </div>
          )}
        </section>
      )}
      {pts.length > 0 && <Summary pts={pts} unit={p.unit} today={today} />}
      {!!p.aliases?.length && <p className="text-xs break-words text-stone-500" data-testid="yazilislar">Aynı ürün sayıldı: {p.aliases.join(", ")}</p>}
      {(pts.length >= 2 || (pts.length === 1 && ref)) && <Chart pts={pts} unit={p.unit} today={today} mref={ref} />}
      {caption && (
        <p data-testid="piyasa-yazi" className={cx("rounded-lg px-3 py-2 text-sm font-medium",
          VERDICT_BOX[[...p.history].filter((h) => h.price != null && h.vs_market).sort((a, b) => a.date.localeCompare(b.date)).pop()?.vs_market?.verdict ?? "normal"])}>{caption}</p>
      )}
      {stores.length === 1 && (
        <p className="rounded-lg bg-stone-50 px-3 py-2 text-xs text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
          Yalnız {ayrilma(maskText(stores[0]))} aldın; başka {isMarket ? "marketten" : "yerden"} alınca karşılaştırırız.</p>
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
                  <div className="font-medium">{dayWord(h.date, today)} · {maskText(chainName(h.store))}</div>
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

// mağaza renkleri (sabit sıra: ilk alınan mağaza 1. renk; doğrulanmış kategorik palet, açık / koyu)
const STORE_STROKE = ["stroke-[#2a78d6] dark:stroke-[#3987e5]", "stroke-[#eb6834] dark:stroke-[#d95926]", "stroke-[#1baf7a] dark:stroke-[#199e70]",
  "stroke-[#eda100] dark:stroke-[#c98500]", "stroke-[#e87ba4] dark:stroke-[#d55181]"];
const STORE_FILL = ["fill-[#2a78d6] dark:fill-[#3987e5]", "fill-[#eb6834] dark:fill-[#d95926]", "fill-[#1baf7a] dark:fill-[#199e70]",
  "fill-[#eda100] dark:fill-[#c98500]", "fill-[#e87ba4] dark:fill-[#d55181]"];
const STORE_BG = ["bg-[#2a78d6] dark:bg-[#3987e5]", "bg-[#eb6834] dark:bg-[#d95926]", "bg-[#1baf7a] dark:bg-[#199e70]",
  "bg-[#eda100] dark:bg-[#c98500]", "bg-[#e87ba4] dark:bg-[#d55181]"];

/** Mağaza başına bir çizgi (tek alımlı mağaza nokta), alımlar etiketli; piyasa tek kesik yatay çizgi + en ucuz işareti. */
function Chart({ pts, unit, today, mref }: { pts: PricePoint[]; unit: string; today: string; mref?: MarketRef | null }) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(340);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => { if (e) setW(Math.max(240, Math.round(e.contentRect.width))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = W < 480 ? 190 : 220, L = 48, R = 16, T = 26, B = 24;
  const series = storeSeries(pts);
  const d0 = pts[0]!.date, d1 = pts[pts.length - 1]!.date;
  const span = daysBetween(d0, d1);
  const x = (pt: PricePoint) => (span > 0 ? L + (daysBetween(d0, pt.date) / span) * (W - L - R) : pts.length === 1 ? (L + W - R) / 2 : L + (pts.indexOf(pt) * (W - L - R)) / (pts.length - 1));
  const vals = [...pts.map((q) => q.price), ...(mref ? [mref.median, mref.min] : [])];
  const ax = niceTicks(Math.min(...vals), Math.max(...vals));
  const y = (v: number) => T + (1 - (v - ax.lo) / (ax.hi - ax.lo || 1)) * (H - T - B);
  const tick = (v: number) => `₺${v.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
  const hidden = tl(1).includes("•");
  const months = pts.map((pt) => ({ pt, ym: pt.date.slice(0, 7) })).filter((m, k, all) => k === 0 || all[k - 1]!.ym !== m.ym);
  // etiket: az alımda hepsi, çokta mağazanın son alımı; üst üste binen atlanır
  const want = pts.length <= 6 ? pts : series.map((s) => s.pts[s.pts.length - 1]!);
  const labels: { pt: PricePoint; lx: number; ly: number }[] = [];
  for (const pt of want) {
    const lx = x(pt), ly = y(pt.price) - 10;
    if (labels.some((l) => Math.abs(l.lx - lx) < 46 && Math.abs(l.ly - ly) < 13)) continue;
    labels.push({ pt, lx, ly });
  }
  const showMin = !!mref && mref.min < mref.median - 0.005;
  const first = pts[0]!, last = pts[pts.length - 1]!;
  return (
    <figure className="space-y-1.5" data-testid="fiyat-grafik">
      <figcaption className="sr-only">{pts.length} alım, {series.length} yer · {dayWord(first.date, today)} → {dayWord(last.date, today)}{mref ? ` · piyasa ortancası ${tick(mref.median)}` : ""}</figcaption>
      <div ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img"
        aria-label={`${series.map((s) => `${s.store}: ${s.pts.map((q) => tick(q.price)).join(", ")}`).join("; ")}`}>
        {ax.ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="stroke-stone-200 dark:stroke-stone-700/70" strokeWidth={1} />
            {!hidden && <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} className="fill-stone-500">{tick(v)}</text>}
          </g>
        ))}
        {mref && (
          <g data-testid="piyasa-cizgi">
            <line x1={L} x2={W - R} y1={y(mref.median)} y2={y(mref.median)} strokeWidth={1.5} strokeDasharray="5 4" className="stroke-stone-500 dark:stroke-stone-400" />
            {!hidden && <text x={L + 4} y={y(mref.median) - 5} fontSize={11} className="fill-stone-600 dark:fill-stone-300">Piyasa ortancası {tl(mref.median)}</text>}
            {showMin && (
              <g data-testid="piyasa-en-ucuz">
                <path d={`M${W - R - 6},${y(mref.min)} l5,-5 l5,5 l-5,5 z`} className="fill-green-600 stroke-white dark:fill-green-400 dark:stroke-stone-900" strokeWidth={1.5} />
                {!hidden && <text x={W - R - 10} y={y(mref.min) + 4} textAnchor="end" fontSize={11} className="fill-green-700 dark:fill-green-400">{mref.cheapest_chain} {tl(mref.min)}</text>}
                <title>{`En ucuz: ${mref.cheapest_chain} ${tl(mref.min)}`}</title>
              </g>
            )}
          </g>
        )}
        {series.map((s) => (
          <g key={s.store} data-testid="magaza-cizgi">
            {s.pts.slice(1).map((pt, k) => {
              const a = s.pts[k]!;
              return <line key={k} x1={x(a)} y1={y(a.price)} x2={x(pt)} y2={y(pt.price)} strokeWidth={2} strokeLinecap="round"
                strokeDasharray={a.estimated || pt.estimated ? "6 5" : undefined} className={STORE_STROKE[s.c]} />;
            })}
            {s.pts.map((pt, k) => (
              <circle key={k} cx={x(pt)} cy={y(pt.price)} r={4.5} strokeWidth={2}
                className={cx("stroke-white dark:stroke-stone-900", pt.estimated ? "fill-white dark:fill-stone-900" : STORE_FILL[s.c])}>
                <title>{`${dayWord(pt.date, today)} · ${pt.store} · ${tl(pt.price)}${pt.estimated ? " (tahmini)" : ""}`}</title>
              </circle>
            ))}
          </g>
        ))}
        {!hidden && labels.map(({ pt, lx, ly }, i) => (
          <text key={i} x={Math.min(Math.max(lx, L + 22), W - R - 22)} y={Math.max(ly, 12)} textAnchor="middle" fontSize={12} fontWeight={600}
            className="fill-stone-800 dark:fill-stone-100">{tl(pt.price)}</text>
        ))}
        {span > 0 && months.map((m) => {
          const mx = x(m.pt);
          return <text key={m.ym} x={mx} y={H - 6} textAnchor={mx < L + 20 ? "start" : mx > W - R - 20 ? "end" : "middle"} fontSize={11} className="fill-stone-500">{monthShort(m.ym)}</text>;
        })}
      </svg>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-600 dark:text-stone-300" data-testid="grafik-lejant">
        {series.map((s) => (
          <span key={s.store} className="inline-flex items-center gap-1.5"><span className={cx("inline-block size-2.5 rounded-full", STORE_BG[s.c])} aria-hidden />{maskText(s.store)}</span>
        ))}
        {mref && <span className="inline-flex items-center gap-1.5"><svg width="20" height="6" aria-hidden><line x1="0" y1="3" x2="20" y2="3" strokeDasharray="5 4" strokeWidth="1.5" className="stroke-stone-500" /></svg> Piyasa ortancası</span>}
        {showMin && <span className="inline-flex items-center gap-1.5"><span className="inline-block size-2 rotate-45 bg-green-600 dark:bg-green-400" aria-hidden /> En ucuz market</span>}
        {per(unit) && <span className="text-stone-500">Birim fiyat ({per(unit).slice(1)})</span>}
      </p>
      {mref && <p className="text-[11px] text-stone-400 dark:text-stone-500" data-testid="piyasa-kaynak">Piyasa: {mref.source === "resmi" ? "Market Fiyatı" : "kullanıcılar"} · {dayWord(mref.day, today)}</p>}
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
  // kullanıcıların bu haftaki fiyatı (yetenek community_quality) önce, sonra ucuzdan pahalıya; en ucuz yeşil
  const sorted = [...(grp?.best ?? [])].sort((a, b) => a.price - b.price).slice(0, 5);
  const best = [...sorted.filter((b) => b.source === "community"), ...sorted.filter((b) => b.source !== "community")];
  const cheapest = sorted[0];
  const newest = newestOfficial(best);
  const brandOn = useCap("shop_brand_size");
  if (brandOn) return <GroupBrandSize g={g} hidden={grp?.hidden_count} officialHere={best.some((b) => b.source === "resmi")} />;
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
            const l = bestLine(b, today, newest);
            const cp = communityParts(b, (n) => maskText(tl(n)));
            return (
              <li key={`${b.chain}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5" data-testid="nereden-satir">
                <span className={cx("tabular-nums", b === cheapest && "font-semibold text-green-700 dark:text-green-400")}>{l.main}{b === cheapest && best.length > 1 ? " · en ucuz" : ""} <OfferBadges b={b} /></span>
                <span className="text-xs text-stone-500">{cp ? cp.main.replace(/^Kullanıcılar \S+ ödüyor/, "kullanıcılar") : l.src}</span>
                {cp && cp.extra.length > 0 && <span className={cx("w-full text-xs", b.differs ? "font-medium text-amber-700 dark:text-amber-400" : "text-stone-500")} data-testid="topluluk-resmi">{cp.extra.join(" · ")}</span>}
              </li>
            );
          })}
        </ul>
      )}
      <HiddenHint count={grp?.hidden_count} />
      <RegionPrompt show={best.some((b) => b.source === "resmi")} />
      <PriceFootnote best={best} today={today} />
    </section>
  );
}

/** Grup ayrıntısı, marka + boy (yetenek shop_brand_size): "Marka fark etmez" + markalar, boy (grubun kendi boyu
 *  varsayılan) → "Mağazalara göre" (Ürünü düzenle penceresiyle aynı bileşen ve metin). */
function GroupBrandSize({ g, hidden, officialHere }: { g: PriceGroupRef; hidden?: number; officialHere: boolean }) {
  const br = useQuery({
    queryKey: ["price-brands", g.key], retry: false, staleTime: 10 * 60_000,
    queryFn: () => send<{ group: string; brands: BrandOpt[] }>(`/api/prices/brands?group=${encodeURIComponent(g.key)}`),
  });
  const [brand, setBrand] = useState<string | null>(null);
  const opts = sizeOptions(g.key, br.data?.brands, brand);
  const [size, setSize] = useState<string | null>(g.key.includes("|") ? g.key : null);
  const key = size && opts.includes(size) ? size : opts[0] ?? (g.key.includes("|") ? g.key : null);
  const money = (n: number) => maskText(tl(n));
  const brands = br.data?.brands ?? [];
  return (
    <section className="space-y-3 rounded-lg border border-stone-200 p-3 dark:border-stone-700" data-testid="nereden-alinir">
      <p className="text-xs text-stone-500">Aynı ürün (marka fark etmez): <b className="text-stone-800 dark:text-stone-100">{g.label}</b></p>
      <h3 className="text-sm font-semibold">Nereden alınır</h3>
      {brands.length > 0 && (
        <div>
          <span className="label" id={`pd-marka-${g.key}-l`}>Marka</span>
          <BrandChips id={`pd-marka-${g.key}`} brands={brands} value={brand} onChange={setBrand} />
        </div>
      )}
      {opts.length > 1 && (
        <div>
          <span className="label" id={`pd-boy-${g.key}`}>Boy</span>
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-labelledby={`pd-boy-${g.key}`} data-testid="boy-cipleri">
            {opts.map((k) => (
              <button key={k} type="button" role="radio" aria-checked={k === key} onClick={() => setSize(k)}
                className={cx("rounded-full border px-3 py-1 text-sm tabular-nums",
                  k === key ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-600"
                    : "border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:text-stone-200 dark:hover:bg-stone-800")}>
                {keySizeLabel(k)}</button>
            ))}
          </div>
        </div>
      )}
      {key ? <OffersList group={key} brand={brand} sizeLabel={keySizeLabel(key)} money={money} />
        : <HiddenHint count={hidden} />}
      <RegionPrompt show={officialHere} />
    </section>
  );
}
