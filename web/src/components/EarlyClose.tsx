import { hataMetni } from "../lib/hataAcikla";
import { CheckCircle2, PiggyBank } from "lucide-react";
import { useState } from "react";
import { call } from "../lib/apiError";
import { ask } from "../lib/ask";
import { cx, maskText, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { isLimited, useBook, useCap, useSave } from "./hooks";
import Modal from "./Modal";

/** Taksit erken kapama (sunucu app/early_close.py, yetenek "card_early_close"): GET /api/cards/summary cards[].early_close
 *  ve GET /api/interest early_close[]. Metinler ve tutarlar sunucudan; faiz gizliyse tutar alanları null.
 *  Mobil src/components/ErkenKapama.tsx aynı akış. */
export interface EarlyCloseInfo {
  period_end: string;
  available: boolean;
  rest: number;
  fee_left: number | null;
  hint: string | null;
  months: { month: string; label: string }[];
  default_month: string | null;
  record: {
    month: string; label: string; amount: number | null; total: number; rest: number; saved: number | null;
    at: string | null; text: string; saved_text: string | null;
  } | null;
  detected: { saved: number | null; verified: boolean; by_line: boolean; text: string } | null;
}

/** Kartın satırında: kazanç tahmini + "Taksitleri erken kapattım", kayıt varsa özeti ve Düzenle, ekstreden tanındıysa
 *  bilgi. compact: yalnız kayıt / tanıma (Özet'teki net durum kartı). */
export default function EarlyCloseLine({ cardNo, info, compact, className }: {
  cardNo: string; info?: EarlyCloseInfo | null; compact?: boolean; className?: string;
}) {
  const on = useCap("card_early_close");
  const canEdit = !isLimited(useBook().data); // sahip ya da tam yetkili üye (sunucu da ayrıca uygular)
  const [open, setOpen] = useState(false);
  if (!on || !info) return null;
  const rec = info.record;
  const det = info.detected;
  if (compact && !rec && !det) return null;
  return (
    <span className={cx("mt-1 block space-y-1", className)} data-testid="erken-kapama">
      {det && (
        <span role="note" className="flex gap-1.5 rounded-md bg-green-50 px-2 py-1 text-xs text-green-900 dark:bg-green-950/40 dark:text-green-100">
          <CheckCircle2 size={13} className="mt-0.5 shrink-0" aria-hidden /><span className="min-w-0">{maskText(det.text)}</span>
        </span>
      )}
      {rec && (
        <span role="note" className="flex gap-1.5 rounded-md bg-green-50 px-2 py-1 text-xs text-green-900 dark:bg-green-950/40 dark:text-green-100">
          <CheckCircle2 size={13} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0">
            {rec.saved_text && <b className="block font-semibold">{maskText(rec.saved_text)}</b>}
            {maskText(rec.text)}
            {canEdit && !compact && <button className="ml-1 font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setOpen(true)}>Düzenle</button>}
          </span>
        </span>
      )}
      {!rec && !compact && info.available && (
        <span className="flex flex-wrap items-baseline gap-x-2 text-xs">
          {info.hint && <span className="flex min-w-0 gap-1.5 text-stone-600 dark:text-stone-300">
            <PiggyBank size={13} className="mt-0.5 shrink-0 text-green-700 dark:text-green-400" aria-hidden />{maskText(info.hint)}</span>}
          {canEdit && <button className="font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setOpen(true)}>Taksitleri erken kapattım</button>}
        </span>
      )}
      {open && <EarlyCloseForm cardNo={cardNo} info={info} onClose={() => setOpen(false)} />}
    </span>
  );
}

/** "Taksitleri erken kapattım" formu (PUT /api/cards/early-close; düzenle aynı uç, geri al DELETE). */
export function EarlyCloseForm({ cardNo, info, onClose }: { cardNo: string; info: EarlyCloseInfo; onClose: () => void }) {
  const ui = useUI();
  const rec = info.record;
  const choices = rec && !info.months.some((m) => m.month === rec.month) ? [{ month: rec.month, label: `${rec.label} ekstresi` }, ...info.months] : info.months;
  const [month, setMonth] = useState(rec?.month ?? info.default_month ?? choices[0]?.month ?? "");
  const [v, setV] = useState(rec?.amount != null ? String(rec.amount).replace(".", ",") : "");
  const [err, setErr] = useState<string | null>(null);
  const save = useSave((body: { month: string; amount: number | null }) => call("PUT", "/api/cards/early-close", { card_no: cardNo, ...body }),
    () => { ui.toast(rec ? "Erken kapama güncellendi" : "Erken kapama kaydedildi"); onClose(); });
  const undo = useSave(() => call("DELETE", `/api/cards/early-close?card_no=${encodeURIComponent(cardNo)}`),
    () => { ui.toast("Erken kapama geri alındı"); onClose(); });
  const submit = () => {
    const n = v.trim() ? parseAmount(v) : null;
    if (n != null && !(n > 0)) { setErr("Tutarı doğru yaz ya da boş bırak"); return; }
    setErr(null);
    save.mutate({ month, amount: n }, { onError: (e) => setErr(hataMetni(e)) });
  };
  return (
    <Modal title="Taksitleri erken kapattım" onClose={onClose} footer={
      <>
        {rec && <button className="btn-ghost text-red-600" disabled={undo.isPending}
          onClick={() => void ask("Erken kapama geri alınsın mı? Kalan taksitler yeniden aylara yayılır.").then((ok) => { if (ok) undo.mutate(undefined, { onError: (e) => setErr(hataMetni(e)) }); })}>Geri al</button>}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending || !month} onClick={submit}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-300">
          Bankan kalan taksitleri tek seferde ekstrene yazar, kalan vade farkı ve vergisi silinir. Seçtiğin ekstre gelene
          kadar Planlama ve Özet buna göre hesaplar.</p>
        <div className="space-y-1">
          <label className="label" htmlFor="ek-ay">Hangi ekstrede gelecek</label>
          <select id="ek-ay" className="input" value={month} onChange={(e) => setMonth(e.target.value)}>
            {choices.map((m) => <option key={m.month} value={m.month}>{m.label}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="ek-tutar">Kapatma tutarı (₺, isteğe bağlı)</label>
          <input id="ek-tutar" className="input text-right" inputMode="decimal" value={v} placeholder={tl(info.rest)}
            onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
          <p className="text-xs text-stone-500">Bankanın söylediği tutar. Boş bırakırsan kalan taksitlerin toplamı ({tl(info.rest)}) yazılır.</p>
        </div>
        {info.hint && <p className="text-xs text-green-800 dark:text-green-300">{maskText(info.hint)}</p>}
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}
