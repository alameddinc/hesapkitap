import { AlertTriangle, Check, CheckCircle2, ChevronDown, FileText, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, type Payable, type Statement } from "../api";
import { cx, dateTR, tl, todayISO, tsDateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

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
  if ((eq.ok === false || recOff) && s.confirmed_at) return "confirmed";
  return eq.ok != null || s.reconciliation_diff != null ? "ok" : null;
}

const Line = ({ l, v, tone }: { l: string; v: string; tone?: string }) => (
  <div className={cx("flex justify-between gap-3", tone)}><span>{l}</span><span className="shrink-0 tabular-nums">{v}</span></div>
);

/** Ekstreler listesinde tek ekstre: başlık (dokununca denklem açılır), durum rozeti, eylemler.
 *  "Kontrol et" durumunda birincil eylemler PDF'i aç ve Doğru, onayla; silme en altta, ikincil. */
export default function StatementCard({ s }: { s: Statement }) {
  const ui = useUI();
  const status = statementStatus(s);
  const [open, setOpen] = useState(status === "check");
  const eq = equation(s);
  const fees = feesOf(s);
  const name = `${s.bank} · ${s.card_no}`;
  const confirm = useSave(() => api.confirmStatement(s.id), () => ui.toast("Onaylandı: rakamlar ekstreyle aynı"));
  const unconfirm = useSave(() => api.unconfirmStatement(s.id), () => ui.toast("Onay geri alındı"));
  const del = useSave(() => api.deleteStatement(s.id), () => ui.toast("Ekstre silindi"));
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
    <li className={cx("card overflow-hidden", status === "check" && "border-amber-300 dark:border-amber-800")}>
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
          {status === "ok" && <span className="inline-flex items-center gap-1 rounded-md bg-green-50 px-1.5 py-0.5 text-[11px] font-semibold text-green-700 dark:bg-green-950/50 dark:text-green-400"><CheckCircle2 size={12} aria-hidden /> Doğrulandı</span>}
          {status === "confirmed" && <span className="inline-flex items-center gap-1 rounded-md bg-green-50 px-1.5 py-0.5 text-[11px] font-semibold text-green-700 dark:bg-green-950/50 dark:text-green-400"><CheckCircle2 size={12} aria-hidden /> Onaylandı</span>}
          {status === "check" && <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"><AlertTriangle size={12} aria-hidden /> Kontrol et</span>}
        </div>
        <ChevronDown size={16} aria-hidden className={cx("mt-1 shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div id={id} className="space-y-3 border-t border-stone-100 px-3 pt-3 pb-3 text-sm dark:border-stone-800">
          {eq.ok != null && (
            <div className="space-y-1.5 rounded-lg bg-stone-50 p-3 text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
              <Line l="Önceki borç" v={tl(s.previous_balance!)} />
              <Line l="− Ödemeler" v={tl(s.payments!)} />
              <Line l={eq.feesIncluded === true && fees > 0 ? `+ Harcamalar (faiz ${tl(fees)} dahil)` : "+ Harcamalar"} v={tl(s.period_activity!)} />
              {eq.feesIncluded === false && <Line l="+ Faiz ve ücret" v={tl(fees)} tone="text-red-600 dark:text-red-400" />}
              <div className="flex justify-between gap-3 border-t border-stone-200 pt-1.5 font-semibold text-stone-900 dark:border-stone-700 dark:text-stone-100">
                <span>= Yeni borç</span>
                <span className={cx("shrink-0 tabular-nums", eq.ok || s.confirmed_at ? "text-green-700 dark:text-green-400" : "text-amber-700 dark:text-amber-300")}>
                  {tl(s.total_debt)} {eq.ok || s.confirmed_at ? "✓" : "⚠"}</span>
              </div>
              {!eq.ok && <Line l="Fark" v={tl(eq.diff!)} tone="text-amber-700 dark:text-amber-300" />}
            </div>
          )}
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {status === "check"
              ? eq.ok === false
                ? `Denklem ${tl(Math.abs(eq.diff!))} tutmadı. PDF'i aç, ekstredeki "Dönem borcu" ve işlemlerle karşılaştır: eksik ya da fazla okunan işlem olabilir. Rakamlar aynıysa "Doğru, onayla" de.`
                : `PDF'i aç ve işlemleri ekstreyle karşılaştır. Rakamlar aynıysa "Doğru, onayla" de.`
              : status === "confirmed" ? `Rakamları ekstreyle karşılaştırıp doğru dedin${s.confirmed_at ? ` (${tsDateTR(s.confirmed_at)})` : ""}.`
              : eq.ok && eq.feesIncluded != null ? "Denklem tuttu: faiz ve ücretle birlikte okunan rakamlar ekstreyle aynı."
              : eq.ok ? "Denklem tuttu: okunan rakamlar ekstreyle aynı." : "Bu ekstrede özet rakamları okunamadı."}
          </p>
          {!!s.reconciliation_diff && Math.abs(s.reconciliation_diff) >= 1 && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              Okunan işlemlerin toplamı ekstre özetinden {tl(Math.abs(s.reconciliation_diff))} {s.reconciliation_diff > 0 ? "fazla" : "eksik"}: bir işlem eksik ya da fazla okunmuş olabilir.
            </p>
          )}
          {fees > 0 && eq.ok == null && (
            <p className="text-xs text-red-600 dark:text-red-400">Bu ekstrede {tl(fees)} faiz ve ücret var.</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {status === "check" ? (<>
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
                <Undo2 size={15} aria-hidden /> Onayı geri al</button>
            )}
            <div className="flex-1" />
            <button className="btn-danger px-2 text-xs" disabled={del.isPending}
              onClick={() => window.confirm(`${s.card_no} ${dateTR(s.period_end)} ekstresi ve içindeki ${s.txn_count} işlem silinsin mi? Yanlış dosyaysa sonra doğrusunu yükleyebilirsin.`) && del.mutate(undefined)}>
              <Trash2 size={14} aria-hidden /> Ekstreyi sil</button>
          </div>
          {(confirm.error || del.error || unconfirm.error) && (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">{((confirm.error || del.error || unconfirm.error) as Error).message}</p>
          )}
        </div>
      )}
    </li>
  );
}
