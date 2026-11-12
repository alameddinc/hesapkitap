import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, ArrowDownRight, ArrowUpRight, ChevronRight } from "lucide-react";
import { useState } from "react";
import { send } from "../lib/apiError";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import { useOpenRow } from "./hooks";
import Modal from "./Modal";
import Segmented from "./Segmented";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";
import { SubLogoBox, type SubLogo } from "./AppSubs";

// Abonelik ayrıntısı (sunucu app/sub_detail.py, yetenek subscription_detail): toplam ödenen (son 12 ay / tümü), ay ay
// tutar (zam/indirim belirgin, dövizlide kur farkı ayrı; kur farkı yalnız TL %3+ değişince, eşik sunucuda), işlemler (dokununca
// mevcut işlem düzenleme penceresi).
// Toplamlar sunucudan; istemci toplamaz. Tutarlar tl() ile (göz kapalıysa ••••).

interface Total { amount: number; count: number; since: string | null; first_date: string | null; fx_text: string | null; text: string | null }
interface Change { kind: "zam" | "indirim" | "kur" | "adet"; diff: number; from: number; to: number; fx_text: string | null }
interface Month { month: string; label: string; amount: number; count: number; fx_text: string | null; change: Change | null; after_cancel: boolean; over: boolean }
interface Txn { uid: string; date: string; month: string; amount: number; label: string; via: "kart" | "hesap"; fx: string | null; description: string; after_cancel: boolean }
export interface SubDetailData {
  kind: "card" | "store"; key: string; title: string; logo: SubLogo; status: string; status_text: string; status_date: string | null;
  amount: number | null; prev_amount: number | null; per: "ay" | "yıl" | null; store_label: string | null; category: string | null;
  totals: { all: Total; last12: Total }; since12: string; months: Month[]; transactions: Txn[];
  fx: boolean; fx_note: string | null; after_cancel: { amount: number; count: number } | null;
}
export type SubRef = { merchant: string } | { app: string };

const refKey = (r: SubRef) => ("merchant" in r ? `m:${r.merchant}` : `a:${r.app}`);
const refQuery = (r: SubRef) => ("merchant" in r ? `merchant=${encodeURIComponent(r.merchant)}` : `app=${encodeURIComponent(r.app)}`);

export function useSubDetail(r: SubRef) {
  return useQuery({ queryKey: ["subscription-detail", refKey(r)], queryFn: () => send<SubDetailData>(`/api/subscriptions/detail?${refQuery(r)}`) });
}

function ChangeBadge({ c }: { c: Change }) {
  if (c.kind === "adet") return null;
  if (c.kind === "kur") {
    return <span className="text-[11px] text-stone-500">kur farkı {c.diff > 0 ? "+" : "−"}{tl(Math.abs(c.diff))}</span>;
  }
  const up = c.kind === "zam";
  return (
    <span className={cx("inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium",
      up ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" : "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300")}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
      {up ? "Zam" : "İndirim"} {c.fx_text ?? `${c.diff > 0 ? "+" : "−"}${tl(Math.abs(c.diff))}`}
    </span>
  );
}

/** Ödemeler: toplam, ay ay, işlemler. Kart aboneliği penceresi ve mağaza aboneliği ayrıntısı bunu kullanır. */
export function SubPayments({ d }: { d: SubDetailData }) {
  const ui = useUI();
  const openRow = useOpenRow();
  const [range, setRange] = useState<"last12" | "all">("last12");
  const tot = d.totals[range];
  const inRange = (ym: string) => range === "all" || ym >= d.since12;
  const months = d.months.filter((m) => inRange(m.month));
  const txns = d.transactions.filter((t) => inRange(t.date.slice(0, 7)));
  const max = Math.max(...months.map((m) => m.amount), 1);
  const open = (t: Txn) => void openRow(t.uid, t.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)), t.month)
    .then((ok) => { if (!ok) ui.toast("İşlem bulunamadı (silinmiş olabilir)"); });

  return (
    <div className="space-y-3 text-sm">
      <Segmented label="Dönem" value={range} onChange={setRange}
        options={[{ value: "last12", label: "Son 12 ay" }, { value: "all", label: "Tümü" }]} />

      <div className="rounded-xl bg-stone-50 p-3 dark:bg-stone-800/60">
        <div className="text-xs text-stone-500">Toplam ödenen</div>
        <div className="text-2xl font-semibold tabular-nums" data-testid="sub-total">{tl(tot.amount)}</div>
        <div className="text-xs text-stone-500">{tot.text ?? "Bu dönemde ödeme yok"}{tot.fx_text ? ` · ${tot.fx_text}` : ""}</div>
        {d.fx_note && <div className="mt-1 text-xs text-stone-500">{d.fx_note}</div>}
      </div>

      {d.after_cancel && (
        <div className="flex items-start gap-2 rounded-lg bg-red-50 p-2.5 text-xs font-medium text-red-700 dark:bg-red-950/40 dark:text-red-300">
          <AlertOctagon size={14} className="mt-0.5 shrink-0" />
          <span>{d.status_date ? `${dateTR(d.status_date)} iptalinden sonra` : "İptalden sonra"} {d.after_cancel.count} kez, toplam {tl(d.after_cancel.amount)} çekildi. İptal tamamlanmamış olabilir; kontrol et, iade iste.</span>
        </div>
      )}

      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Ay ay</h3>
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {months.map((m) => (
            <li key={m.month} className="py-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0">
                  <span className="font-medium">{m.label}</span>
                  {(m.count > 1 || m.fx_text) && <span className="text-xs text-stone-500"> · {[m.count > 1 && `${m.count} çekim`, m.fx_text].filter(Boolean).join(" · ")}</span>}
                </span>
                <span className={cx("shrink-0 font-semibold tabular-nums", (m.after_cancel || m.over) && "text-red-600")}>{tl(m.amount)}</span>
              </div>
              <div className="mt-1 h-1 rounded-full bg-stone-100 dark:bg-stone-800" aria-hidden="true">
                <div className={cx("h-1 rounded-full", m.change?.kind === "zam" || m.after_cancel || m.over ? "bg-red-500" : "bg-blue-500/70")}
                  style={{ width: `${Math.max(4, (m.amount / max) * 100)}%` }} />
              </div>
              {(m.change || m.after_cancel || m.over) && (
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {m.change && <ChangeBadge c={m.change} />}
                  {m.after_cancel && <span className="text-[11px] font-medium text-red-600">İptalden sonra çekildi</span>}
                  {m.over && <span className="text-[11px] font-medium text-amber-600">Paket düşmemiş görünüyor</span>}
                </div>
              )}
            </li>
          ))}
          {!months.length && <li className="py-2 text-xs text-stone-500">Son 12 ayda ödeme yok. Tümü'ne bak.</li>}
        </ul>
      </section>

      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">İşlemler</h3>
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {txns.map((t) => (
            <li key={t.uid}>
              <button type="button" onClick={() => open(t)} data-uid={t.uid}
                className="flex min-h-11 w-full items-center gap-2 py-1.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
                aria-label={`${dateTR(t.date)} ${t.label} ${t.description}: işlemi aç`}>
                <span className="min-w-0 flex-1">
                  <span className="block">{dateTR(t.date)} · {t.label}</span>
                  <span className="block truncate text-xs text-stone-500">{t.description}{t.fx ? ` · ${t.fx}` : ""}</span>
                  {t.after_cancel && <span className="block text-[11px] font-medium text-red-600">İptalden sonra</span>}
                </span>
                <span className={cx("shrink-0 font-semibold tabular-nums", t.after_cancel && "text-red-600")}>{tl(t.amount)}</span>
                <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Mağaza aboneliği ayrıntısının içinde: ödemeler bölümü (yükleniyor/hata durumuyla). */
export function SubPaymentsFor({ r }: { r: SubRef }) {
  const q = useSubDetail(r);
  return (
    <>
      <SorguDurumu q={q} etiket="Ödemeler yükleniyor"><SatirIskelet n={4} /></SorguDurumu>
      {q.data && <SubPayments d={q.data} />}
    </>
  );
}

/** Kart aboneliği (Tasarruf > Abonelikler satırı): başlık + ödemeler. */
export default function SubDetailModal({ r, title, onClose }: { r: SubRef; title: string; onClose: () => void }) {
  const q = useSubDetail(r);
  const d = q.data;
  return (
    <Modal title={d?.title ?? title} onClose={onClose}>
      <div className="space-y-3">
        {d && (
          <div className="flex items-center gap-3">
            <SubLogoBox logo={d.logo} size={44} />
            <div className="min-w-0 flex-1">
              <div className={cx("font-semibold", d.status === "iptal" && "line-through")}>{d.title}</div>
              <div className="text-xs text-stone-500">
                {d.status_text}{d.status_date ? ` · ${dateTR(d.status_date)}` : ""}{d.category ? ` · ${d.category}` : ""}{d.store_label ? ` · ${d.store_label}` : ""}
              </div>
            </div>
            {d.amount != null && (
              <div className="shrink-0 text-right font-semibold tabular-nums">
                {d.prev_amount != null && <span className="block text-xs font-normal text-stone-400 line-through">{tl(d.prev_amount)}</span>}
                {tl(d.amount)}<span className="text-xs font-normal text-stone-500">{d.per ? ` /${d.per}` : ""}</span>
              </div>
            )}
          </div>
        )}
        <SorguDurumu q={q} etiket="Abonelik yükleniyor"><SatirIskelet n={4} /></SorguDurumu>
        {d && <SubPayments d={d} />}
      </div>
    </Modal>
  );
}
