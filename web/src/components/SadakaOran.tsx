import { useQuery } from "@tanstack/react-query";
import { Percent } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { call } from "../lib/apiError";
import { cx, periodLabel, tlShown } from "../format";
import { routeVisible, useBook, useCap, useMeta } from "./hooks";
import Segmented from "./Segmented";

/** GET /api/donations/ratio (sunucu yeteneği sadaka_ratio). */
export interface RatioMonth {
  month: string; label: string; short: string; sadaka: number; income: number | null; ratio: number | null;
  note: string | null; zakat: number | null; partial: boolean;
}
export interface SadakaRatio {
  available: boolean; months: RatioMonth[]; sentence: string | null; ratio_available: boolean;
  basis?: "kisi" | "defter"; min_income?: number; note?: string | null;
}

const MASK = "••••";
const pctTR = (x: number) => `%${x.toLocaleString("tr-TR", { maximumFractionDigits: 1, minimumFractionDigits: x < 10 ? 1 : 0 })}`;

/** Sadakanın gelire oranı: ay ay ince çubuk (tek ölçü: %; tutarlar ipucunda ve tabloda), tablo görünümü, nötr özet
 *  cümlesi. Tutarlar sayfanın gözüne bağlı (show). Öneri yok. */
export default function SadakaOran({ show }: { show: boolean }) {
  const on = useCap("sadaka_ratio");
  const book = useBook().data;
  const meta = useMeta().data;
  const visible = !!meta && routeVisible(book, "zekat", meta.modules);
  const [months, setMonths] = useState<"6" | "12">("6");
  const [view, setView] = useState<"grafik" | "tablo">("grafik");
  const q = useQuery({ queryKey: ["sadaka-ratio", months], queryFn: () => call<SadakaRatio>("GET", `/api/donations/ratio?months=${months}`),
    enabled: on && visible, retry: false });
  const d = q.data;
  if (!on || !visible || !d?.available) return null;
  const m = (x: number | null) => (x == null ? "—" : show ? tlShown(x) : MASK);
  // tabloda kuruşsuz (dar ekranda sütunlar sığsın)
  const m0 = (x: number | null) => (x == null ? "—" : show ? `₺${Math.round(x).toLocaleString("tr-TR")}` : MASK);
  const anyZakat = d.months.some((r) => (r.zakat ?? 0) > 0);
  const chart = d.ratio_available && view === "grafik";
  return (
    <section className="card space-y-3 p-4" data-testid="sadaka-oran">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium"><Percent size={16} aria-hidden /> Gelirine oranı</h3>
        <div className="flex flex-wrap gap-2">
          <Segmented label="Dönem" value={months} onChange={setMonths} options={[{ value: "6", label: "6 ay" }, { value: "12", label: "12 ay" }]} />
          {d.ratio_available && <Segmented label="Görünüm" value={view} onChange={setView} options={[{ value: "grafik", label: "Grafik" }, { value: "tablo", label: "Tablo" }]} />}
        </div>
      </div>
      {d.sentence && <p className="text-sm" data-testid="sadaka-oran-cumle">{d.sentence}</p>}
      {d.note && <p className="text-xs text-stone-500">{d.note}</p>}
      {chart ? <OranGrafik rows={d.months} m={m} /> : <OranTablo rows={d.months} m={m0} ratio={d.ratio_available} zakat={anyZakat} />}
      <p className="text-xs text-stone-500">Gelir: Özet'teki gelir, faiz geliri hariç. Zekât bu orana dahil değil.
        {d.ratio_available ? " Geliri çok az ya da hiç olmayan ayda oran hesaplanmaz." : ""}</p>
    </section>
  );
}

function OranTablo({ rows, m, ratio, zakat }: { rows: RatioMonth[]; m: (x: number | null) => string; ratio: boolean; zakat: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm whitespace-nowrap tabular-nums">
        <thead className="text-xs text-stone-500">
          <tr className="text-left"><th className="py-1 pr-2 font-medium">Ay</th><th className="py-1 pr-2 text-right font-medium">Sadaka</th>
            {ratio && <><th className="py-1 pr-2 text-right font-medium">Gelir</th><th className="py-1 text-right font-medium">Oran</th></>}
            {zakat && <th className="py-1 pl-2 text-right font-medium text-stone-400">Zekât (ayrı)</th>}</tr>
        </thead>
        <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
          {[...rows].reverse().map((r) => (
            <tr key={r.month}>
              <td className="py-1.5 pr-2">{periodLabel(r.month, true)}{r.partial && <span className="text-xs text-stone-500"> · sürüyor</span>}</td>
              <td className="py-1.5 pr-2 text-right">{m(r.sadaka)}</td>
              {ratio && <><td className="py-1.5 pr-2 text-right">{m(r.income)}</td>
                <td className="py-1.5 text-right">{r.ratio != null ? pctTR(r.ratio) : <span className="text-xs text-stone-500">{r.note ?? "—"}</span>}</td></>}
              {zakat && <td className="py-1.5 pl-2 text-right text-stone-500">{r.zakat ? m(r.zakat) : "—"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Tek seri, tek eksen (%): ince çubuk (4px yuvarlak uç, tabana oturur), silik ızgara, çubuk başına üzerine gelince ipucu
 *  (dokunma alanı çubuktan geniş: bütün sütun). Oranı olmayan ay tabanda kısa çizgi. Renk: açıkta blue-600, koyuda
 *  blue-500 (doğrulandı: kart yüzeyinde ≥3:1). */
function OranGrafik({ rows, m }: { rows: RatioMonth[]; m: (x: number | null) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  // genişlik kabın genişliği: yazılar her ekranda aynı boyda kalsın (viewBox ölçeklenmesin)
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(320);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(240, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = 170, L = 34, B = 22, T = 16;
  const max = Math.max(1, ...rows.map((r) => r.ratio ?? 0)) * 1.15;
  const step = niceStep(max);
  const top = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const slot = (W - L) / rows.length;
  const bw = Math.min(16, slot * 0.42);
  const y = (v: number) => T + (H - B - T) * (1 - v / top);
  const lastIdx = rows.map((r) => r.ratio != null && !r.partial).lastIndexOf(true);
  const h = hover != null ? rows[hover] : null;
  return (
    <div ref={box} className="relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img" aria-label="Sadakanın gelire oranı, aylara göre">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="stroke-stone-200 dark:stroke-stone-800" strokeWidth={1} />
            <text x={L - 6} y={y(t) + 3} textAnchor="end" className="fill-stone-500 text-[11px]">%{t.toLocaleString("tr-TR")}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cx0 = L + slot * i + slot / 2;
          const base = y(0);
          return (
            <g key={r.month} tabIndex={0} role="button" aria-label={`${periodLabel(r.month)}: ${r.ratio != null ? pctTR(r.ratio) : r.note ?? "oran yok"}`}
              onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} onClick={() => setHover(i)} className="outline-none">
              <rect x={L + slot * i} y={T} width={slot} height={H - T} fill="transparent" />
              {r.ratio != null && r.ratio > 0 ? (
                <path d={barPath(cx0 - bw / 2, y(r.ratio), bw, base - y(r.ratio))}
                  className={cx("fill-blue-600 dark:fill-blue-500", r.partial && "opacity-45", hover === i && "fill-blue-700 dark:fill-blue-400")} />
              ) : (
                <line x1={cx0 - bw / 2} x2={cx0 + bw / 2} y1={base - 1} y2={base - 1} strokeWidth={2} strokeLinecap="round"
                  className="stroke-stone-300 dark:stroke-stone-600" />
              )}
              {i === lastIdx && r.ratio != null && (
                <text x={cx0} y={y(r.ratio) - 4} textAnchor="middle" className="fill-stone-700 text-[11px] font-medium dark:fill-stone-300">{pctTR(r.ratio)}</text>
              )}
              <text x={cx0} y={H - 7} textAnchor="middle" className={cx("text-[11px]", hover === i ? "fill-stone-900 dark:fill-stone-100" : "fill-stone-500")}>{periodLabel(r.month, true).split(" ")[0]}</text>
            </g>
          );
        })}
      </svg>
      {h && hover != null && (
        <div role="status" className="pointer-events-none absolute top-0 z-10 w-44 rounded-lg border border-stone-200 bg-white p-2 text-xs shadow-lg dark:border-stone-700 dark:bg-stone-800"
          style={{ left: `clamp(0px, calc(${((L + slot * hover + slot / 2) / W) * 100}% - 5.5rem), calc(100% - 11rem))` }}>
          <div className="font-medium">{periodLabel(h.month)}{h.partial ? " · sürüyor" : ""}</div>
          <div className="mt-0.5 tabular-nums">{h.ratio != null ? <>Oran <b>{pctTR(h.ratio)}</b></> : <span className="text-stone-500">{h.note ?? "Oran yok"}</span>}</div>
          <div className="tabular-nums text-stone-600 dark:text-stone-300">Sadaka {m(h.sadaka)}</div>
          {h.income != null && <div className="tabular-nums text-stone-600 dark:text-stone-300">Gelir {m(h.income)}</div>}
        </div>
      )}
    </div>
  );
}

/** Üstü 4px yuvarlak, tabanı düz çubuk. */
function barPath(x: number, y0: number, w: number, h: number) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y0 + h}V${y0 + r}Q${x},${y0} ${x + r},${y0}H${x + w - r}Q${x + w},${y0} ${x + w},${y0 + r}V${y0 + h}Z`;
}

function niceStep(max: number) {
  const raw = max / 3;
  const p = 10 ** Math.floor(Math.log10(raw));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
