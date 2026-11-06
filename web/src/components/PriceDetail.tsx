// Fiyatlar → ürüne tıklayınca: anlaşılır özet (az alımda "₺138,00 → ₺129,00 (−%7)"), yeterli alımda grafik (her alım
// işaretli, ilk/son değer yazılı, yuvarlak eksen, yalnız alım olan aylar), "Alımların" listesi (fişe gider), tek market
// notu; sunucu "price_groups" verirse markası fark etmeyen grup ve "Nereden alınır" (kendi + topluluk fiyatı).
import { useQuery } from "@tanstack/react-query";
import { ReceiptText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx, maskText, tl, todayISO } from "../format";
import { send } from "../lib/apiError";
import { ayrilma, changePct, dayWord, daysBetween, monthShort, niceTicks, pctWord, pricedPoints, type PricePoint } from "../lib/priceView";
import { useUI } from "../ui";
import { useMeta, useOpenRow } from "./hooks";

export interface PriceHistory {
  date: string; price: number | null; store: string; branch: string | null; qty: number; name: string;
  /** yeni sunucu: kaydın uid'i ("m12") ve fiş kaydı; tahmini fiyat (fişte birim fiyat yoktu) */
  uid?: string | null; entry_id?: number | null; estimated?: boolean;
}
export interface PriceGroupRef { key: string; label: string }
export interface GroupBest { chain: string; price: number; brand?: string | null; source: "own" | "community"; date?: string | null; n?: number | null }
export interface PriceGroup { key: string; label: string; best: GroupBest[] }

const per = (u: string) => (u === "kg" ? "/kg" : u === "lt" ? "/lt" : "");
const qtyText = (q: number, unit: string) => `${q.toLocaleString("tr-TR", { maximumFractionDigits: 3 })} ${unit === "adet" ? "adet" : unit}`;

/** "BİM ₺34,50 (Torku)" + kaynak: "senin fişin, 3 gün" / "topluluk, 14 gün" */
export function bestLine(b: GroupBest, today: string) {
  const age = b.date ? daysBetween(b.date, today) : null;
  const ago = age == null ? "" : age <= 0 ? ", bugün" : `, ${age} gün`;
  return { main: `${maskText(b.chain)} ${tl(b.price)}${b.brand ? ` (${b.brand})` : ""}`, src: `${b.source === "community" ? "topluluk" : "senin fişin"}${ago}` };
}

export default function PriceDetail({ p }: { p: { product: string; unit: string; history: PriceHistory[]; group?: PriceGroupRef | null } }) {
  const ui = useUI();
  const openRow = useOpenRow();
  const caps = useMeta().data?.capabilities;
  const today = todayISO();
  const pts = pricedPoints(p.history);
  const stores = [...new Set(p.history.map((h) => h.store))];
  const group = caps?.includes("price_groups") ? p.group ?? null : null;
  const rows = [...p.history].map((h, i) => ({ h, i })).sort((a, b) => b.h.date.localeCompare(a.h.date) || b.i - a.i);
  const open = (h: PriceHistory) => {
    const uid = h.uid ?? (h.entry_id != null ? `m${h.entry_id}` : null);
    if (uid) void openRow(uid, h.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)))
      .then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı (silinmiş olabilir)"); });
  };

  return (
    <div className="space-y-3 px-4 pb-4" data-testid="fiyat-detay">
      {pts.length >= 3 ? <Chart pts={pts} unit={p.unit} today={today} /> : pts.length > 0 && <Summary pts={pts} unit={p.unit} today={today} />}
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

/** 1–2 alım: grafik yerine tek cümle. */
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
      <p className="text-xs text-stone-500">{dayWord(a.date, today)} → {dayWord(b.date, today)} · {a.store === b.store ? maskText(a.store) : `${maskText(a.store)} → ${maskText(b.store)}`}</p>
    </div>
  );
}

/** ≥3 alım: her alım bir nokta, ilk ve son değer yazılı; tahmini fiyata giden parça kesikli. */
function Chart({ pts, unit, today }: { pts: PricePoint[]; unit: string; today: string }) {
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
  const prices = pts.map((x) => x.price);
  const ax = niceTicks(Math.min(...prices), Math.max(...prices));
  const x = (i: number) => (pts.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (pts.length - 1));
  const y = (v: number) => T + (1 - (v - ax.lo) / (ax.hi - ax.lo || 1)) * (H - T - B);
  const anyEst = pts.some((x) => x.estimated);
  // ay etiketi: ayın ilk alımının altında (yalnız alım olan aylar)
  const months = pts.map((pt, i) => ({ i, ym: pt.date.slice(0, 7) })).filter((m, k, all) => k === 0 || all[k - 1]!.ym !== m.ym);
  const first = pts[0]!, last = pts[pts.length - 1]!;
  const c = changePct(first.price, last.price);
  const tick = (v: number) => `₺${v.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`;
  const hidden = tl(1).includes("•");
  return (
    <figure className="space-y-1">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs text-stone-500">
        <span>{pts.length} alım · {dayWord(first.date, today)} → {dayWord(last.date, today)}</span>
        <span className={cx("font-semibold tabular-nums", c > 0 ? "text-red-600 dark:text-red-400" : c < 0 ? "text-green-700 dark:text-green-400" : "")}>{pctWord(c)}</span>
      </figcaption>
      <div ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img"
        aria-label={`${pts.length} alımda fiyat ${tick(first.price)} liradan ${tick(last.price)} liraya`}>
        {ax.ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="stroke-stone-200 dark:stroke-stone-700" strokeWidth={1} />
            {!hidden && <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} className="fill-stone-500">{tick(v)}</text>}
          </g>
        ))}
        {pts.slice(1).map((pt, k) => {
          const a = pts[k]!;
          return <line key={k} x1={x(k)} y1={y(a.price)} x2={x(k + 1)} y2={y(pt.price)} strokeWidth={2.5} strokeLinecap="round"
            strokeDasharray={a.estimated || pt.estimated ? "6 5" : undefined} className="stroke-blue-600 dark:stroke-blue-400" />;
        })}
        {pts.map((pt, i) => (
          <circle key={i} cx={x(i)} cy={y(pt.price)} r={4.5} strokeWidth={2}
            className={cx("stroke-blue-600 dark:stroke-blue-400", pt.estimated ? "fill-white dark:fill-stone-900" : "fill-blue-600 dark:fill-blue-400")}>
            <title>{`${dayWord(pt.date, today)} · ${pt.store} · ${tl(pt.price)}${pt.estimated ? " (tahmini)" : ""}`}</title>
          </circle>
        ))}
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
        {anyEst && <span className="inline-flex items-center gap-1.5"><svg width="22" height="6" aria-hidden><line x1="0" y1="3" x2="22" y2="3" strokeDasharray="5 4" strokeWidth="2.5" className="stroke-blue-600 dark:stroke-blue-400" /></svg> Kesikli: tahmini fiyat</span>}
      </p>
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
  const best = [...(grp?.best ?? [])].sort((a, b) => a.price - b.price);
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
              <li key={`${b.chain}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className={cx("tabular-nums", i === 0 && "font-semibold text-green-700 dark:text-green-400")}>{l.main}{i === 0 && best.length > 1 ? " · en ucuz" : ""}</span>
                <span className="text-xs text-stone-500">{l.src}{b.source === "community" && b.n ? ` · ${b.n} kişi` : ""}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
