import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, CheckCircle2, ChevronRight, CreditCard, Info, Layers, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ApiError, send } from "../lib/apiError";
import { cx, dateTR, tl, periodLabel } from "../format";
import { isLimited, useBook, useCap, useOpenRow } from "../components/hooks";
import { EarlyCloseForm, type EarlyCloseInfo } from "../components/EarlyClose";
import Segmented from "../components/Segmented";
import { useUI } from "../ui";
import SorguDurumu, { KartIskelet, SatirIskelet } from "../components/SorguDurumu";

/** GET /api/interest?months=N (yetenek interest_page): months 0 = tüm geçmiş. Ortak defterde faiz gizliyse hidden. */
type InterestType = "gecikme" | "akdi" | "vade_farki" | "vergi" | "nakit_avans" | "kar_payi" | "ucret" | "faiz";
interface InterestItem {
  uid: string; id: number; date: string; month: string; statement_id: number; period_end: string; due_date: string | null;
  bank: string; card_no: string; last4: string; card: string; description: string;
  type: InterestType; type_label: string; amount: number; reason: string;
}
interface InterestStatement {
  statement_id: number; card: string; card_no: string; period_end: string; due_date: string | null; month: string;
  total: number; reason: string | null; reason_kind: "gec" | "eksik" | "odenmedi" | null;
}
interface InterestReport {
  hidden: boolean; months: number; from: string | null; to: string;
  total: number; interest: number; tax: number; fee: number; count: number;
  by_month: { month: string; total: number }[];
  by_card: { card_no: string; last4: string; bank: string; card: string; total: number; count: number; last_date: string }[];
  statements: InterestStatement[];
  items: InterestItem[];
  tips: { key: string; title: string; detail: string }[];
  income: { total: number; items: { uid: string; date: string; description: string; amount: number }[] };
  /** kalan taksitlerinde vade farkı olan / erken kapatılmış kartlar (yetenek card_early_close; eski sunucuda yok) */
  early_close?: (EarlyCloseInfo & { card_no: string; bank: string; last4: string; card: string })[];
}

const dayMonth = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("tr-TR", { day: "numeric", month: "long" });
const H2 = ({ children }: { children: React.ReactNode }) => <h2 className="px-1 text-xs font-semibold tracking-wide text-stone-500 uppercase">{children}</h2>;

/** Faiz ve ücretler (mobil screens/Faiz.tsx): hangi karttan, ne zaman, neden faiz ödendi ve nasıl önlenir.
 *  Kalem → işlem ayrıntısı, ekstre başlığı → o ekstre, kart → o kartın kalemleri. */
export default function Interest() {
  const ui = useUI();
  const on = useCap("interest_page");
  const book = useBook().data;
  const openRow = useOpenRow();
  const [months, setMonths] = useState<"12" | "0">("12");
  const [pick, setPick] = useState<string | null>(null); // grafikte seçilen ay: kalemler o aya süzülür
  const [card, setCard] = useState<string | null>(null);
  const [kapama, setKapama] = useState<string | null>(null); // "Erken kapattım" formu açık kart
  const canEdit = !isLimited(book); // sahip ya da tam yetkili üye
  const q = useQuery({ queryKey: ["interest", months], queryFn: () => send<InterestReport>(`/api/interest?months=${months}`), enabled: on });
  const r = q.data;
  const hidden = !!book?.hide_fees || !!r?.hidden;
  const old = !on || (q.error instanceof ApiError && q.error.status === 404);
  const kart = card && r ? r.by_card.find((c) => c.card_no === card) : undefined;
  const openTxn = (uid: string, date: string) =>
    void openRow(uid, date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row))).then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı"); });
  const openStatement = (id: number) => { window.location.hash = `/ekstreler?ekstre=${id}`; window.scrollTo(0, 0); };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        {card && <button className="btn-ghost -ml-2 mb-1 px-2 py-1 text-sm" onClick={() => setCard(null)}><ArrowLeft size={16} /> Faiz ve ücretler</button>}
        <h1 className="text-xl font-semibold">{kart ? kart.card : "Faiz ve ücretler"}</h1>
        <p className="text-sm text-stone-500 dark:text-stone-400">
          {card ? "Bu kartın faiz, vergi ve ücret kalemleri." : "Hangi karttan, ne zaman ve neden; bir dahakine nasıl önlenir."}</p>
      </div>

      {old && <p role="note" className="flex gap-2 rounded-lg bg-blue-50 p-3 text-sm text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
        <Info size={16} className="mt-0.5 shrink-0" aria-hidden />Sunucun eski bir sürüm; bazı özellikler kapalı. Sunucunu güncelleyince açılır.</p>}
      {!old && hidden && (
        <div className="card flex flex-col items-center gap-2 p-6 text-center">
          <Lock size={24} className="text-stone-400" aria-hidden />
          <p className="font-semibold">Bu defterde gizli</p>
          <p className="text-sm text-stone-500">Defter sahibi faiz ve ücretleri ortak defterde gizlemiş.</p>
        </div>
      )}
      {!old && !hidden && (
        <>
          {!card && <div className="max-w-xs"><Segmented label="Dönem" value={months} onChange={(v) => { setMonths(v); setPick(null); }}
            options={[{ value: "12", label: "Son 12 ay" }, { value: "0", label: "Tüm geçmiş" }]} /></div>}
          <SorguDurumu q={q} etiket="Faiz ve ücretler yükleniyor"><div className="space-y-3"><KartIskelet satir={3} /><SatirIskelet n={3} /></div></SorguDurumu>
          {r && (card ? renderCard({ r, card }) : renderMain({ r }))}
        </>
      )}
      {kapama && (() => {
        const ec = r?.early_close?.find((c) => c.card_no === kapama);
        return ec ? <EarlyCloseForm cardNo={kapama} info={ec} onClose={() => setKapama(null)} /> : null;
      })()}
    </div>
  );

  function renderMain({ r }: { r: InterestReport }) {
    const span = r.months > 0 ? `Son ${r.months} ayda` : "Şimdiye dek";
    if (r.total < 0.01) return (
      <>
        <section className="flex items-start gap-3 rounded-2xl bg-green-50 p-4 dark:bg-green-950/30" data-testid="faiz-yok">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-green-600 text-white"><Check size={22} aria-hidden /></span>
          <span className="min-w-0">
            <span className="block text-lg font-semibold">Bu dönemde faiz ödemedin</span>
            <span className="block text-sm text-stone-600 dark:text-stone-300">
              {r.months > 0 ? `Son ${r.months} ayın` : "Hiçbir"} ekstresinde faiz, vade farkı ya da kart ücreti görünmüyor. Borcun tamamını son ödeme gününe kadar ödemeye devam et.</span>
          </span>
        </section>
        {renderEarly({ r })}
        {renderIncome({ r })}
      </>
    );
    const vals = r.by_month.slice(-12);
    const shown = pick ? r.items.filter((i) => i.month === pick) : r.items;
    const max = Math.max(1, ...vals.map((x) => x.total));
    const paid = vals.filter((x) => x.total > 0);
    return (
      <>
        <section className="card space-y-3 p-4" data-testid="faiz-ozet">
          <p className="text-sm text-stone-500">{span} ödediğin faiz, vergi ve ücret</p>
          <p className="text-3xl font-bold tabular-nums text-red-600 dark:text-red-400">{tl(r.total)}</p>
          <div className="grid grid-cols-3 gap-2">
            {([["Faiz", r.interest], ["Vergi", r.tax], ["Ücret", r.fee]] as const).map(([k, v]) => (
              <span key={k} className="rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-800/60">
                <span className="block text-xs text-stone-500">{k}</span>
                <span className="block text-sm font-semibold tabular-nums">{tl(v)}</span>
              </span>
            ))}
          </div>
          {vals.length > 1 && (
            <>
              <div role="img" aria-label={paid.length ? `Faiz ödenen aylar: ${paid.map((x) => `${periodLabel(x.month)} ${tl(x.total)}`).join(", ")}` : "Faiz ödenen ay yok"}>
                <div className="flex h-16 items-end gap-1 border-b border-stone-200 dark:border-stone-700">
                  {vals.map((x) => (
                    <span key={x.month} className={cx("min-w-0 flex-1 rounded-t", x.total > 0 ? "bg-red-500" : "bg-stone-200 dark:bg-stone-700", pick && pick !== x.month && "opacity-35")}
                      style={{ height: x.total > 0 ? Math.max(5, Math.round((x.total / max) * 60)) : 2 }} />
                  ))}
                </div>
                <div aria-hidden className="mt-1 flex gap-1">
                  {vals.map((x) => <span key={x.month} className="min-w-0 flex-1 overflow-hidden text-center text-[10px] text-stone-400">{periodLabel(x.month).slice(0, 1)}</span>)}
                </div>
              </div>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Aya göre süz">
                {paid.map((x) => (
                  <button key={x.month} aria-pressed={pick === x.month} onClick={() => setPick(pick === x.month ? null : x.month)}
                    className={cx("rounded-full border px-3 py-1 text-xs", pick === x.month ? "border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950/40 dark:text-blue-200" : "border-stone-300 dark:border-stone-700")}>
                    {periodLabel(x.month, true)} <span className="tabular-nums">{tl(x.total)}</span></button>
                ))}
              </div>
            </>
          )}
        </section>

        {r.tips.length > 0 && (
          <section className="space-y-1.5" aria-label="Nasıl önlenir">
            <H2>Nasıl önlenir</H2>
            <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
              {r.tips.map((t) => (
                <li key={t.key} className="flex items-start gap-3 p-3" data-testid="faiz-oneri">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400"><ShieldCheck size={17} aria-hidden /></span>
                  <span className="min-w-0"><span className="block font-medium">{t.title}</span>
                    <span className="block text-sm text-stone-600 dark:text-stone-400">{t.detail}</span></span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {renderEarly({ r })}

        {r.by_card.length > 0 && (
          <section className="space-y-1.5">
            <H2>Kartlara göre</H2>
            <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
              {r.by_card.map((c) => (
                <li key={c.card_no}>
                  <button className="flex w-full items-center gap-3 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" data-testid="faiz-kart"
                    onClick={() => { setCard(c.card_no); window.scrollTo(0, 0); }} aria-label={`${c.card}: ${c.count} kalem, ${tl(c.total)}`}>
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400"><CreditCard size={17} aria-hidden /></span>
                    <span className="min-w-0 flex-1"><span className="block font-medium break-words">{c.card}</span>
                      <span className="block text-xs text-stone-500">{c.count} kalem · son {dayMonth(c.last_date)}</span></span>
                    <span className="shrink-0 font-semibold tabular-nums text-red-600 dark:text-red-400">{tl(c.total)}</span>
                    <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {renderItems({ r, items: shown, title: pick ? `${periodLabel(pick)} kalemleri` : "Kalemler" })}
        {renderIncome({ r })}
      </>
    );
  }

  function renderCard({ r, card }: { r: InterestReport; card: string }) {
    const items = r.items.filter((i) => i.card_no === card);
    const c = r.by_card.find((x) => x.card_no === card);
    if (!items.length || !c) return (
      <div className="card flex flex-col items-center gap-2 p-6 text-center">
        <Check size={24} className="text-green-600" aria-hidden />
        <p className="font-semibold">Bu kartta faiz yok</p>
        <p className="text-sm text-stone-500">Son 12 ayda bu kartın ekstrelerinde faiz ya da ücret görünmüyor.</p>
      </div>
    );
    return (
      <>
        <section className="card space-y-1 p-4">
          <p className="text-sm text-stone-500">Son {r.months || 12} ayda bu karttan</p>
          <p className="text-3xl font-bold tabular-nums text-red-600 dark:text-red-400">{tl(c.total)}</p>
          <p className="text-sm text-stone-500">{items.length} kalem · {new Set(items.map((i) => i.statement_id)).size} ekstre</p>
        </section>
        {renderItems({ r, items, title: "Kalemler" })}
      </>
    );
  }

  /** ekstre ekstre kalemler (başlık → ekstre, satır → işlem) */
  function renderItems({ r, items, title }: { r: InterestReport; items: InterestItem[]; title: string }) {
    const st = new Map(r.statements.map((s) => [s.statement_id, s]));
    const groups = [...new Set(items.map((i) => i.statement_id))].map((id) => ({
      id, s: st.get(id), rows: items.filter((i) => i.statement_id === id),
    }));
    if (!groups.length) return null;
    return (
      <section className="space-y-1.5">
        <H2>{title}</H2>
        {groups.map(({ id, s, rows }) => {
          const head = s ?? { card: rows[0].card, period_end: rows[0].period_end, due_date: rows[0].due_date, total: null, reason: null };
          return (
            <ul key={id} className="card divide-y divide-stone-100 overflow-hidden dark:divide-stone-800" data-testid="faiz-ekstre">
              <li>
                <button className="flex w-full items-start gap-2 bg-stone-50 p-3 text-left hover:bg-stone-100 dark:bg-stone-800/50 dark:hover:bg-stone-800"
                  onClick={() => openStatement(id)} aria-label={`${head.card} ekstresi, kesim ${dayMonth(head.period_end)}. Ekstreyi aç`}>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium break-words">{head.card} · ekstre {dayMonth(head.period_end)}</span>
                    {head.reason && <span className="block text-xs text-red-600 dark:text-red-400">{head.reason}</span>}
                    {!head.reason && head.due_date && <span className="block text-xs text-stone-500">Son ödeme {dayMonth(head.due_date)}</span>}
                  </span>
                  {head.total != null && <span className="shrink-0 font-semibold tabular-nums">{tl(head.total)}</span>}
                  <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
                </button>
              </li>
              {rows.map((i, n) => (
                <li key={i.uid}>
                  <button className="flex w-full items-start gap-2 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" data-testid="faiz-kalem"
                    onClick={() => openTxn(i.uid, i.date)} aria-label={`${i.type_label}, ${dayMonth(i.date)}, ${tl(i.amount)}. ${i.reason}`}>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium">{i.type_label}</span>
                      <span className="block text-xs break-words text-stone-500">{dateTR(i.date)} · {i.description}</span>
                      {/* aynı neden bir kez (ekstre başlığında ya da ilk satırında) */}
                      {i.reason !== head.reason + "." && !rows.slice(0, n).some((x) => x.reason === i.reason) &&
                        <span className="block text-xs text-stone-400">{i.reason}</span>}
                    </span>
                    <span className="shrink-0 tabular-nums">{tl(i.amount)}</span>
                    <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          );
        })}
      </section>
    );
  }

  /** kalan taksitlerde vade farkı: kart başına "en çok ₺X ödemezsin" + "Erken kapattım"; kayıt / ekstreden tanıma */
  function renderEarly({ r: rep }: { r: InterestReport }) {
    const list = rep.early_close ?? [];
    if (!list.length) return null;
    return (
      <section className="space-y-1.5" data-testid="faiz-erken-kapama">
        <H2>Taksit erken kapama</H2>
        <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
          {list.map((c) => {
            const done = c.record ?? c.detected;
            return (
              <li key={c.card_no} className="flex items-start gap-3 p-3" data-testid="faiz-erken-kart">
                <span className={cx("flex size-8 shrink-0 items-center justify-center rounded-lg",
                  done ? "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400" : "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300")}>
                  {done ? <CheckCircle2 size={17} aria-hidden /> : <Layers size={17} aria-hidden />}</span>
                <span className="min-w-0 flex-1">
                  <span className="block font-medium break-words">{c.card}</span>
                  {c.detected && <span className="block text-sm text-green-800 dark:text-green-300">{c.detected.text}</span>}
                  {c.record && <>
                    {c.record.saved_text && <span className="block text-sm font-medium text-green-800 dark:text-green-300">{c.record.saved_text}</span>}
                    <span className="block text-sm text-stone-600 dark:text-stone-400">{c.record.text}</span>
                  </>}
                  {!done && c.hint && <span className="block text-sm text-stone-600 dark:text-stone-400">{c.hint}</span>}
                </span>
                {canEdit && (c.record || c.available) && (
                  <button className={cx("shrink-0 text-sm", c.record ? "btn-ghost px-2 py-1" : "btn-primary px-3 py-1.5")} onClick={() => setKapama(c.card_no)}>
                    {c.record ? "Düzenle" : "Erken kapattım"}</button>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  /** istemeden gelen faiz geliri (arındırma) */
  function renderIncome({ r }: { r: InterestReport }) {
    if (!(r.income.total > 0)) return null;
    return (
      <section className="space-y-1.5" data-testid="faiz-geliri">
        <H2>Hesabına işleyen faiz</H2>
        <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
          <li className="p-3 text-sm text-stone-600 dark:text-stone-400">
            Hesabına {tl(r.income.total)} faiz işlemiş görünüyor. Faizden uzak durmak istiyorsan bu tutarı ihtiyaç sahibine vererek arındırabilir, hesap türünü (katılım ya da faizsiz vadesiz) kontrol edebilirsin.</li>
          {r.income.items.map((x) => (
            <li key={x.uid}>
              <button className="flex w-full items-center gap-2 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => openTxn(x.uid, x.date)}>
                <span className="min-w-0 flex-1"><span className="block break-words">{x.description}</span><span className="block text-xs text-stone-500">{dayMonth(x.date)}</span></span>
                {/* kazanç gibi (yeşil +) değil: arındırılacak tutar */}
                <span className="flex shrink-0 flex-col items-end gap-0.5">
                  <span className="tabular-nums text-amber-700 dark:text-amber-300">{tl(x.amount)}</span>
                  <span className="rounded bg-amber-50 px-1.5 py-px text-[11px] font-semibold text-amber-800 dark:bg-amber-950/50 dark:text-amber-300">arındırılacak</span>
                </span>
                <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }
}
