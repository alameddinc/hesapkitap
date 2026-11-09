import { useState } from "react";
import { parseAmount } from "../format";
import Modal from "./Modal";
import PaidWith, { PAID_WITH_START, paidWithBody, type PaidWithState } from "./PaidWith";

export type ZakatPayBody = { amount: number; pay_date: string } & ReturnType<typeof paidWithBody>;

/** "Zekâtı ödedim" tek adımda: tutar ve tarih dolu gelir, "Nasıl ödedin?" sorulur; kayıt gider olarak da yazılır.
 *  fixedAmount: tutar değişmez (gider olarak görünmeyen eski ödemeye gider açarken). Mobil OdeSheet/PaySheet ile aynı. */
export default function ZakatPay({ title, note, amount, date, fixedAmount, busy, onClose, onSave }: {
  title: string; note?: string; amount: number; date: string; fixedAmount?: boolean; busy?: boolean;
  onClose: () => void; onSave: (b: ZakatPayBody) => void;
}) {
  const [v, setV] = useState(amount > 0 ? String(amount).replace(".", ",") : "");
  const [day, setDay] = useState(date);
  const [paid, setPaid] = useState<PaidWithState>(PAID_WITH_START);
  const n = parseAmount(v);
  return (
    <Modal title={title} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(n > 0) || !day || busy}
        onClick={() => onSave({ amount: n, pay_date: day, ...paidWithBody(paid) })}>Kaydet</button>}>
      <div className="space-y-3">
        {note && <p className="text-sm text-stone-600 dark:text-stone-400">{note}</p>}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="zp-amt">Tutar</label>
            <input id="zp-amt" className="input text-right" inputMode="decimal" value={v} disabled={fixedAmount}
              onChange={(e) => setV(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="zp-date">Tarih</label>
            <input id="zp-date" type="date" className="input" value={day} onChange={(e) => setDay(e.target.value)} />
          </div>
        </div>
        <PaidWith id="zp-paid" value={paid} onChange={setPaid} />
      </div>
    </Modal>
  );
}
