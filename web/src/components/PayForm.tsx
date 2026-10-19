import { useState } from "react";
import { api, type Payable, type Payment } from "../api";
import { dateTR, parseAmount, tl } from "../format";
import { useSave } from "./hooks";
import Modal from "./Modal";
import Segmented from "./Segmented";

const today = () => new Date().toISOString().slice(0, 10);

/** "Ödedim": kart ekstresi veya fatura ödemesini işaretle. */
export default function PayForm({ p, onClose, onSaved }: { p: Payable; onClose: () => void; onSaved?: (m: string) => void }) {
  // ödenmişi düzenlerken ödenen tutar, kısmi/ödenmemişte toplam borç
  const [amount, setAmount] = useState(String(p.status === "odendi" ? p.paid_amount ?? p.amount : p.amount));
  const [date, setDate] = useState(p.paid_at ?? today());
  const [payment, setPayment] = useState<Payment>("havale");
  const amt = parseAmount(amount);
  const partial = amt + 0.01 < p.amount;

  const save = useSave(async () => {
    if (!(amt >= 0)) throw new Error("Tutar geçersiz");
    if (p.type === "statement") return api.payStatement(p.id!, amt, date);
    return api.payBill(p.id!, amt, date, payment);
  }, () => { onSaved?.(`${p.title} ödendi olarak işaretlendi`); onClose(); });
  const undo = useSave(async () => (p.type === "statement" ? api.unpayStatement(p.id!) : api.unpayBill(p.id!)),
    () => { onSaved?.("Ödeme geri alındı"); onClose(); });

  return (
    <Modal title="Ödedim" onClose={onClose} footer={
      <>
        {p.paid_at && <button className="btn-danger" onClick={() => undo.mutate(undefined)}>Ödemeyi geri al</button>}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(undefined)}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        <div className="rounded-lg bg-stone-100 p-3 text-sm dark:bg-stone-800">
          <div className="flex justify-between gap-2"><b>{p.title}</b><b className="tabular-nums">{tl(p.amount)}</b></div>
          <div className="mt-1 text-xs text-stone-500">{p.subtitle} · son ödeme {dateTR(p.due_date)}</div>
        </div>
        <div>
          <label className="label" htmlFor="p-amt">Ödenen tutar (₺)</label>
          <input id="p-amt" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <div className="mt-2 flex gap-2">
            <button type="button" className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
              onClick={() => setAmount(String(p.amount))}>Tamamı</button>
            {p.min_payment != null && p.min_payment < p.amount && (
              <button type="button" className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                onClick={() => setAmount(String(p.min_payment))}>Asgari ({tl(p.min_payment)})</button>
            )}
          </div>
          {partial && p.type === "statement" && (
            <p className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              Kalan {tl(p.amount - amt)} bir sonraki ekstreye faiziyle (aylık ~%3,75 + vergiler) devreder.
            </p>
          )}
        </div>
        <div>
          <label className="label" htmlFor="p-date">Ödeme tarihi</label>
          <input id="p-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        {p.type === "bill" && (
          <div>
            <span className="label">Nasıl ödendi</span>
            <Segmented value={payment} onChange={setPayment} options={[
              { value: "havale", label: "Havale / otomatik" },
              { value: "kredi_karti", label: "Kredi kartı" },
              { value: "nakit", label: "Nakit" },
            ]} />
            <p className="mt-1 text-xs text-stone-500">
              {payment === "kredi_karti"
                ? "Gider olarak eklenir; ekstre gelince karttaki işlemle eşleşir, iki kez sayılmaz."
                : "Bu ayın giderlerine eklenir."}
            </p>
          </div>
        )}
        {p.type === "statement" && (
          <p className="text-xs text-stone-500">Kart ekstresi ödemesi gider olarak eklenmez; harcamalar zaten ekstreden sayılıyor.</p>
        )}
      </div>
    </Modal>
  );
}
