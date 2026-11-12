import { hataMetni } from "../lib/hataAcikla";
import { AlertTriangle, Check, CheckCircle2, ChevronDown, FileText, ListPlus, ListTree, RefreshCw, Trash2, Undo2 } from "lucide-react";
import { TERIM } from "../lib/terimler";
import { ask } from "../lib/ask";
import { useState, useEffect, useRef } from "react";
import { api, type Payable, type Statement } from "../api";
import { cx, dateTR, maskText, tl, todayISO, tsDateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import { RereadBanner, RereadSheet } from "./StatementReread";
import { AddRowSheet, EditRowsSheet } from "./StatementFix";
import { BILESEN, DUZELT, type GrupKey } from "../lib/ekstreDuzelt";

/** Ekstredeki faiz ve ücret (sunucunun fees alanı; eski sunucuda interest_fees). */
export const feesOf = (s: Statement) => s.fees ?? s.interest_fees ?? 0;

/** Denklemi sunucu kurar (app/parsers/base.py equation): faizin harcamaların içinde mi ayrı mı olduğu ve fark.
 *  ok: null (özet rakamları okunamadı) | true | false. */
export function equation(s: Statement): { ok: boolean | null; diff: number | null; feesIncluded: boolean | null } {
  const has = s.previous_balance != null && s.payments != null && s.period_activity != null;
  const diff = has ? s.equation_diff ?? null : null;
  return { ok: diff == null ? null : Math.abs(diff) < 1, diff, feesIncluded: s.fees_included ?? null };
}

/** Durum: sunucunun needs_check'i (denklem ya da okunan işlemler tutmuyor, onay yok); onaylıysa "confirmed". */
export function statementStatus(s: Statement): "ok" | "confirmed" | "check" | null {
  if (s.needs_check) return "check";
  const eq = equation(s);
  const recOff = !!s.reconciliation_diff && Math.abs(s.reconciliation_diff) >= 1;
  if ((eq.ok === false || recOff || !!s.check_kind) && s.confirmed_at) return "confirmed";
  return eq.ok != null || s.reconciliation_diff != null ? "ok" : null;
}

const SECONDARY = "btn border border-stone-300 text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800";

/** Denklem satırı; onOpen verilirse dokununca o rakamı oluşturan satırlar açılır. */
const Line = ({ l, v, tone, onOpen }: { l: string; v: string; tone?: string; onOpen?: () => void }) => onOpen ? (
  <button type="button" onClick={onOpen} aria-label={DUZELT.grupAc(l.replace(/^[+−-]\s*/, ""))}
    className={cx("-mx-1 flex w-[calc(100%+0.5rem)] justify-between gap-3 rounded px-1 text-left underline decoration-dotted underline-offset-4 hover:bg-stone-100 dark:hover:bg-stone-700/60", tone)}>
    <span>{l}</span><span className="shrink-0 tabular-nums">{v}</span></button>
) : (
  <div className={cx("flex justify-between gap-3", tone)}><span>{l}</span><span className="shrink-0 tabular-nums">{v}</span></div>
);

/** Ödeme durumu (Ödemeler ile aynı): Ödendi · ₺X ödendi (kısmi) · Gecikti · (bekliyor: rozet yok). */
function paidChip(s: Statement): { text: string; tone: string } | null {
  if (!s.due_date || s.total_debt <= 0) return null;
  if (s.paid_amount != null && s.paid_amount >= s.total_debt - 0.01) return { text: "Ödendi", tone: "bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-400" };
  if (s.paid_amount) return { text: `${tl(s.paid_amount)} ödendi`, tone: "bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300" };
  if (s.due_date < todayISO()) return { text: "Gecikti", tone: "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300" };
  return null;
}

/** Ekstreler listesinde tek ekstre: başlık (dokununca denklem açılır), durum rozeti, eylemler.
 *  "Rakamlar tutmadı" durumunda birincil eylemler PDF'i aç ve Doğru, onayla; silme en altta, ikincil. */
export default function StatementCard({ s }: { s: Statement }) {
  const ui = useUI();
  const status = statementStatus(s);
  // #/ekstreler?ekstre=<id> (Yapılacaklar → doğruluk ayrıntısı): o ekstre açık gelir ve görünür yere kayar
  const focused = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("ekstre") === String(s.id);
  const [open, setOpen] = useState(status === "check" || focused || !!s.reread_rows);
  const [reread, setReread] = useState(false); // şablonla yeniden okuma farkı penceresi
  const [fix, setFix] = useState<"add" | "rows" | null>(null); // eksik satır ekle / satırları düzenle
  const [odak, setOdak] = useState<GrupKey[] | null>(null); // denklemde dokunulan rakamın satırları
  const rows = (g: GrupKey[] | null) => s.can_fix ? () => { setOdak(g); setFix("rows"); } : undefined;
  const chip = paidChip(s);
  const liRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) liRef.current?.scrollIntoView({ block: "start" });
  }, [focused]);
  const eq = equation(s);
  const fees = feesOf(s);
  const name = `${s.bank} · ${s.card_no}`;
  const confirm = useSave(() => api.confirmStatement(s.id), () => ui.toast("Onaylandı: rakamlar ekstreyle aynı"), { inline: true });
  const unconfirm = useSave(() => api.unconfirmStatement(s.id), () => ui.toast("Onay geri alındı"), { inline: true });
  const del = useSave(() => api.deleteStatement(s.id), () => ui.toast("Ekstre silindi"), { inline: true });
  const pdf = `/api/statements/${s.id}/file`;
  // "Ödedim" penceresi (Payables ile aynı): ekstreden ödenecek kalem
  const paid = s.paid_amount != null && s.paid_amount >= s.total_debt - 0.01;
  const payable: Payable | null = s.due_date ? {
    type: "statement", id: s.id, title: `${s.bank} ${s.card_no}`, subtitle: `Ekstre · kesim ${dateTR(s.period_end).slice(0, 5)}`,
    amount: s.total_debt, min_payment: s.min_payment, due_date: s.due_date,
    days_left: Math.round((new Date(`${s.due_date}T00:00:00`).getTime() - new Date(`${todayISO()}T00:00:00`).getTime()) / 864e5),
    paid_amount: s.paid_amount ?? null, paid_at: s.paid_at ?? null,
    status: paid ? "odendi" : s.paid_amount ? "kismi" : s.due_date < todayISO() ? "gecikti" : "bekliyor",
  } : null;
  const id = `st-${s.id}`;

  return (
    <li ref={liRef} className={cx("card scroll-mt-16 overflow-hidden", status === "check" && "border-amber-300 dark:border-amber-800",
      focused && "ring-2 ring-blue-500")}>
      <button className="flex w-full items-start gap-3 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
        aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <div className="min-w-0 flex-1">
          <div className="font-medium break-words">{name}</div>
          <div className="text-xs text-stone-500 dark:text-stone-400">
            Kesim {dateTR(s.period_end)} · son ödeme {s.due_date ? dateTR(s.due_date) : "—"} · {s.txn_count} işlem
            {fees > 0 && <span className="text-red-600 dark:text-red-400"> · faiz ve ücret {tl(fees)}</span>}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="font-semibold tabular-nums">{tl(s.total_debt)}</span>
          {status === "ok" && <span className="inline-flex items-center gap-1 rounded-md bg-green-50 px-1.5 py-0.5 text-[11px] font-semibold text-green-700 dark:bg-green-950/50 dark:text-green-400" title={TERIM.dogrulandi.aciklama}><CheckCircle2 size={12} aria-hidden /> {DUZELT.rozetTuttu}</span>}
          {status === "confirmed" && <span className="inline-flex items-center gap-1 rounded-md bg-stone-100 px-1.5 py-0.5 text-[11px] font-semibold text-stone-700 dark:bg-stone-800 dark:text-stone-300" title={TERIM.onaylandi.aciklama}><CheckCircle2 size={12} aria-hidden /> {DUZELT.rozetOnay}</span>}
          {status === "check" && <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"><AlertTriangle size={12} aria-hidden /> {s.fix_badge ?? TERIM.rakamlarTutmadi.ad}</span>}
          {chip && <span className={cx("rounded-md px-1.5 py-0.5 text-[11px] font-semibold", chip.tone)}>{chip.text}</span>}
        </div>
        <ChevronDown size={16} aria-hidden className={cx("mt-1 shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div id={id} className="space-y-3 border-t border-stone-100 px-3 pt-3 pb-3 text-sm dark:border-stone-800">
          {!!s.reread_rows && !(status === "check" && s.check_kind) && (
            <RereadBanner sid={s.id} bank={s.bank} last4={(s.card_no.replace(/\D/g, "") || s.card_no).slice(-4)} rows={s.reread_rows}
              onOpen={() => setReread(true)} />
          )}
          {eq.ok != null && (
            <div className="space-y-1.5 rounded-lg bg-stone-50 p-3 text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
              <Line l="Önceki borç" v={tl(s.previous_balance!)} />
              <Line l="− Ödemeler" v={tl(s.payments!)} onOpen={rows(BILESEN.odeme)} />
              <Line l={eq.feesIncluded === true && fees > 0 ? `+ Harcamalar (faiz ${tl(fees)} dahil)` : "+ Harcamalar"} v={tl(s.period_activity!)} onOpen={rows(BILESEN.harcama)} />
              {eq.feesIncluded === false && <Line l="+ Faiz ve ücret" v={tl(fees)} tone="text-red-600 dark:text-red-400" onOpen={rows(BILESEN.faiz)} />}
              {s.refunds_separate && s.refund_total != null && <Line l={DUZELT.iadeSatiri} v={tl(s.refund_total)} onOpen={rows(BILESEN.iade)} />}
              <div className="flex justify-between gap-3 border-t border-stone-200 pt-1.5 font-semibold text-stone-900 dark:border-stone-700 dark:text-stone-100">
                <span>= Yeni borç</span>
                <span className={cx("shrink-0 tabular-nums", eq.ok || s.confirmed_at ? "text-green-700 dark:text-green-400" : "text-amber-700 dark:text-amber-300")}>
                  {tl(s.total_debt)} {eq.ok || s.confirmed_at ? "✓" : "⚠"}</span>
              </div>
              {!eq.ok && <Line l="Fark" v={tl(eq.diff!)} tone="text-amber-700 dark:text-amber-300" />}
            </div>
          )}
          {status === "check" && s.fix_title ? (
            // özet doğru ama işlem listesi tutmuyor ("rows") ya da özet tutmuyor ("summary"): tek cümle + ne eksik / fazla
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
              role="status" data-testid="ekstre-fark">
              <div className="font-semibold">{maskText(s.fix_title)}</div>
              {s.fix_hint && <div className="mt-0.5 text-xs">{maskText(s.fix_hint.text)}</div>}
            </div>
          ) : (
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {status === "check"
                ? eq.ok === false
                  ? `Rakamlar ${tl(Math.abs(eq.diff!))} tutmadı. PDF'i aç, ekstredeki "Dönem borcu" ve işlemlerle karşılaştır: eksik ya da fazla okunan işlem olabilir. Rakamlar aynıysa "Doğru, onayla" de.`
                  : `PDF'i aç ve işlemleri ekstreyle karşılaştır. Rakamlar aynıysa "Doğru, onayla" de.`
                : status === "confirmed" ? DUZELT.onayMetni(s.confirmed_at ? tsDateTR(s.confirmed_at) : null)
                : eq.ok && eq.feesIncluded != null ? "Rakamlar tuttu: faiz ve ücretle birlikte okunan rakamlar ekstreyle aynı (uygulama kontrol etti)."
                : eq.ok ? "Rakamlar tuttu: okunan rakamlar ekstreyle aynı (uygulama kontrol etti)." : "Bu ekstrede özet rakamları okunamadı."}
            </p>
          )}
          {!(status === "check" && s.fix_title) && !s.confirmed_at && !!s.reconciliation_diff && Math.abs(s.reconciliation_diff) >= 1 && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              Okunan işlemlerin toplamı ekstre özetinden {tl(Math.abs(s.reconciliation_diff))} {s.reconciliation_diff > 0 ? "fazla" : "eksik"}: bir işlem eksik ya da fazla okunmuş olabilir.
            </p>
          )}
          {fees > 0 && eq.ok == null && (
            <p className="text-xs text-red-600 dark:text-red-400">Bu ekstrede {tl(fees)} faiz ve ücret var.</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {status === "check" && s.check_kind ? (<>
              {/* birincil: şablon varsa onunla düzelt, yoksa eksik satır ekle; "Rakamlar doğru, onayla" ikincil */}
              {!!s.reread_rows && (
                <button className="btn-primary" onClick={() => setReread(true)} data-testid="ekstre-sablonla">
                  <RefreshCw size={15} aria-hidden /> {DUZELT.sablonla(s.reread_rows)}</button>
              )}
              {s.can_fix && (<>
                <button className={s.reread_rows ? SECONDARY : "btn-primary"} onClick={() => setFix("add")} data-testid="ekstre-satir-ekle">
                  <ListPlus size={15} aria-hidden /> {DUZELT.satirEkle}</button>
                <button className={SECONDARY} onClick={() => { setOdak(null); setFix("rows"); }} data-testid="ekstre-satirlar">
                  <ListTree size={15} aria-hidden /> {DUZELT.satirlar}</button>
              </>)}
              <a className="btn-ghost" href={pdf} target="_blank" rel="noreferrer"><FileText size={15} aria-hidden /> {DUZELT.pdf}</a>
              <button className="btn-ghost" disabled={confirm.isPending} onClick={() => confirm.mutate(undefined)} data-testid="ekstre-onayla">
                <CheckCircle2 size={15} aria-hidden /> {DUZELT.onayla}</button>
            </>) : status === "check" ? (<>
              <a className="btn border border-stone-300 text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800"
                href={pdf} target="_blank" rel="noreferrer"><FileText size={15} aria-hidden /> PDF'i aç</a>
              <button className="btn-primary" disabled={confirm.isPending} onClick={() => confirm.mutate(undefined)}>
                <CheckCircle2 size={15} aria-hidden /> Doğru, onayla</button>
            </>) : (
              <a className="btn-ghost" href={pdf} target="_blank" rel="noreferrer"><FileText size={15} aria-hidden /> PDF'i aç</a>
            )}
            {payable && s.total_debt > 0 && (paid || s.paid_amount ? (
              <button className="btn-ghost" onClick={() => ui.openPay(payable)}>
                <Check size={15} aria-hidden /> {paid ? `Ödendi ${s.paid_at ? dateTR(s.paid_at) : ""}` : `${tl(s.paid_amount!)} ödendi`} · düzelt</button>
            ) : (
              <button className="btn border border-stone-300 text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800"
                onClick={() => ui.openPay(payable)}><Check size={15} aria-hidden /> Ödedim</button>
            ))}
            {status === "confirmed" && (
              <button className="btn-ghost" disabled={unconfirm.isPending} onClick={() => unconfirm.mutate(undefined)}>
                <Undo2 size={15} aria-hidden /> {DUZELT.yenidenKontrol}</button>
            )}
          </div>
          {/* Silme onay/ödeme düğmelerinden ayrı satırda: yanlış dokunuşla yanındaki düğme yerine basılmasın. */}
          <div className="flex justify-end border-t border-stone-100 pt-2 dark:border-stone-800">
            <button className="btn-danger px-2 text-xs" disabled={del.isPending}
              onClick={() => void ask(`${s.card_no} ${dateTR(s.period_end)} ekstresi ve içindeki ${s.txn_count} işlem silinsin mi?\n\n`
                + "İşlemlerde yaptığın kategori ve not düzeltmeleri de gider. Bu ekstredeki bir işlemle eşleşmiş elle girdiğin kayıtlar yeniden ayrı gider olarak sayılır.\n\n"
                + "Yanlış dosyaysa sonra doğrusunu yükleyebilirsin.", { confirm: "Kalıcı olarak sil", irreversible: true }).then((ok) => { if (ok) del.mutate(undefined); })}>
              <Trash2 size={14} aria-hidden /> Ekstreyi sil</button>
          </div>
          {(confirm.error || del.error || unconfirm.error) && (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">{hataMetni(confirm.error || del.error || unconfirm.error)}</p>
          )}
        </div>
      )}
      {reread && <RereadSheet sid={s.id} onClose={() => setReread(false)} />}
      {fix === "add" && <AddRowSheet s={s} onClose={() => setFix(null)} />}
      {fix === "rows" && <EditRowsSheet s={s} odak={odak} onClose={() => { setFix(null); setOdak(null); }} />}
    </li>
  );
}
