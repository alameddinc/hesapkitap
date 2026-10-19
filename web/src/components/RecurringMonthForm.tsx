import { useState } from "react";
import { api } from "../api";
import { monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";

export interface RecurringMonth {
  rid: number;
  month: string;
  description: string;
  amount: number;          // o ay için beklenen (aya özel ya da şablon)
  template: number;        // şablon tutar
  kind: "gelir" | "gider" | string;
  mode: "confirm" | "plan"; // confirm: gerçekleşti, tutarı gir · plan: ileriki ay için beklenen tutar
}

/** Değişken sabit kalemler (ör. brüt maaş): onaylarken gerçek tutar, ileriki aylar için aya özel tutar. */
export default function RecurringMonthForm({ r, onClose }: { r: RecurringMonth; onClose: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState(String(r.amount));
  const n = parseAmount(amount);
  const confirm = useSave(() => api.confirmRecurring(r.rid, r.month, n), () => {
    ui.toast(`${r.description} ${monthLabel(r.month)} için ${tl(n)} olarak onaylandı`);
    onClose();
  });
  const plan = useSave((v: number | null) => fetch(`/api/recurring/${r.rid}/months/${r.month}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ amount: v }),
  }).then((x) => { if (!x.ok) throw new Error("Kaydedilemedi"); }), () => { ui.toast("Planlama güncellendi"); onClose(); });
  const skip = useSave(() => api.skipRecurring(r.rid, r.month), () => { ui.toast(`${r.description} bu ay atlandı`); onClose(); });
  const valid = Number.isFinite(n) && n >= 0;

  return (
    <Modal title={`${r.description} · ${monthLabel(r.month)}`} onClose={onClose} footer={
      <>
        {r.mode === "confirm"
          ? <button className="btn-ghost" onClick={() => skip.mutate(undefined)}>Bu ay yok</button>
          : r.amount !== r.template && <button className="btn-ghost" onClick={() => plan.mutate(null)}>Şablona dön</button>}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={!valid}
          onClick={() => (r.mode === "confirm" ? confirm.mutate(undefined) : plan.mutate(n))}>
          {r.mode === "confirm" ? "Onayla" : "Kaydet"}
        </button>
      </>
    }>
      <div className="space-y-3">
        <label className="label" htmlFor="rm-amt">
          {r.mode === "confirm"
            ? `${r.kind === "gelir" ? "Yatan" : "Ödenen"} tutar (₺)`
            : "Bu ay beklenen tutar (₺)"}
        </label>
        <input id="rm-amt" className="input text-right text-lg" inputMode="decimal" autoFocus value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <p className="text-xs text-stone-500">
          Şablon: {tl(r.template)}.{" "}
          {r.mode === "confirm"
            ? "Tutar her ay değişiyorsa (ör. brüt maaşta net) bu ay yatanı yaz; şablon değişmez."
            : "Sadece bu ay için geçerli; diğer aylar şablonla hesaplanır."}
        </p>
      </div>
    </Modal>
  );
}
