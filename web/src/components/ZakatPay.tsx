import { useState } from "react";
import { parseAmount } from "../format";
import { altinAsimi, nasilDurumu, odemeDuzeltGovde, type AltinVarlik, type KayitliOdeme, type NasilGovde } from "../lib/nasilOdedin";
import Modal, { kirliyseSor } from "./Modal";
import PaidWith, { PAID_WITH_START, paidWithBody, usePaidWithGold, type PaidWithState } from "./PaidWith";

export type ZakatPayBody = { amount?: number; pay_date?: string } & NasilGovde;

/** "Zekâtı ödedim" tek adımda: tutar ve tarih dolu gelir, "Nasıl ödedin?" sorulur; kayıt gider olarak da yazılır.
 *  fixedAmount: tutar değişmez (gider olarak görünmeyen eski ödemeye gider açarken). edit: kayıtlı ödemeyi düzelt (tutar,
 *  tarih, ödeme şekli; onSave yalnız değişenleri alır → PATCH). golds: altınla ödemede düşülebilecek altın varlıkları.
 *  Mobil OdeSheet/PaySheet ile aynı. */
export default function ZakatPay({ title, note, amount, date, fixedAmount, busy, onClose, onSave, edit, golds }: {
  title: string; note?: string; amount: number; date: string; fixedAmount?: boolean; busy?: boolean;
  onClose: () => void; onSave: (b: ZakatPayBody) => void; edit?: KayitliOdeme; golds?: AltinVarlik[];
}) {
  const gold = usePaidWithGold();
  const start = edit ? nasilDurumu(edit) : PAID_WITH_START;
  const [v, setV] = useState(amount > 0 ? String(amount).replace(".", ",") : "");
  const [day, setDay] = useState(date);
  const [paid, setPaid] = useState<PaidWithState>(start);
  const n = parseAmount(v);
  const methodLocked = !!edit && (!gold || edit.method_editable === false);
  // düzeltmede bu ödemenin düştüğü altın önce geri konur: o varlıktan düşülebilecek miktar o kadar artar (hepsi
  // verildiyse varlık listede yok, ödemenin kaydından eklenir)
  const mine = edit?.asset_id && edit.asset_qty ? { id: edit.asset_id, qty: edit.asset_qty } : null;
  const goldList = !mine ? golds : [
    ...(golds ?? []).map((g) => g.id === mine.id ? { ...g, qty: g.qty + mine.qty } : g),
    ...((golds ?? []).some((g) => g.id === mine.id) ? [] : [{ id: mine.id, label: edit?.asset_label ?? "Altın", qty: mine.qty, unit: edit?.asset_unit ?? "" }]),
  ];
  const ok = n > 0 && !!day && !busy && !altinAsimi(paid, goldList);
  const kaydet = () => {
    if (!ok) return;
    onSave(edit ? odemeDuzeltGovde(edit, { amount: n, pay_date: day, nasil: paid }, gold) : { amount: n, pay_date: day, ...paidWithBody(paid, gold) });
  };
  // kaydedilmemiş değişiklik (UX denetimi 09.11): tutar, tarih ya da ödeme şekli değiştiyse kapatırken sorulur
  const dirty = v !== (amount > 0 ? String(amount).replace(".", ",") : "") || day !== date || JSON.stringify(paid) !== JSON.stringify(start);
  return (
    <Modal title={title} onClose={onClose} dirty={dirty && !busy} onSave={kaydet} footer={<>
      <button className="btn-ghost" onClick={() => void kirliyseSor(dirty && !busy, onClose, kaydet)}>Vazgeç</button>
      <button className="btn-primary flex-1 justify-center" disabled={!ok} onClick={kaydet}>Kaydet</button></>}>
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
        {methodLocked
          ? edit?.method_editable === false && <p className="text-xs text-stone-500">Ödeme şekli ekstre ya da hesap dökümündeki hareketten geliyor; oradan değişir.</p>
          : <PaidWith id="zp-paid" value={paid} onChange={setPaid} golds={goldList} />}
      </div>
    </Modal>
  );
}
