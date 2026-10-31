import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Payable, type Payment } from "../api";
import { dateTR, parseAmount, tl, todayISO } from "../format";
import AccountSelect from "./AccountSelect";
import type { Debt } from "./Debts";
import { useSave } from "./hooks";
import Modal from "./Modal";
import Segmented from "./Segmented";

/** "Ödedim": kart ekstresi, fatura ya da vadeli borç ödemesini işaretle (tamamı / asgari / kısmi, tarih;
 *  faturada nasıl ödendiği, borçta hangi hesaptan). Mobil: components/planlama/OdemeSheet.tsx PaySheet. */
export default function PayForm({ p, onClose, onSaved }: { p: Payable; onClose: () => void; onSaved?: (m: string) => void }) {
  const paid = p.status === "odendi";
  const debt = p.type === "debt";
  // ekstre/fatura "ödenen tutar"ı yazar (üstüne eklemez); borçta ödeme eklenir → varsayılan kalanın tamamı
  const left = Math.max(0, p.amount - (p.paid_amount ?? 0));
  const full = debt ? left : p.amount;
  // düzeltme: ödenmiş (ya da kısmi ödenmiş) ekstrede kayıtlı ödenen tutar
  const [amount, setAmount] = useState(String(paid || (p.type === "statement" && (p.paid_amount ?? 0) > 0) ? p.paid_amount ?? p.amount : full));
  const [warn, setWarn] = useState<string | null>(null); // sunucu uyarısı (ör. dökümden gelen ödeme tutarı farklı)
  const [date, setDate] = useState((p.paid_at ?? todayISO()).slice(0, 10));
  const [payment, setPayment] = useState<Payment>("havale");
  // borç: parayı hangi hesaptan ödedin (varsayılan: borcun kayıtlı hesabı)
  const debts = useQuery({ queryKey: ["debts"], queryFn: () => fetch("/api/debts").then((r) => r.json() as Promise<Debt[]>), enabled: debt });
  const [acc, setAcc] = useState<number | null | undefined>(undefined);
  const debtAcc = acc !== undefined ? acc : debts.data?.find((d) => d.id === p.id)?.account_id ?? null;
  // ekstre: ödemenin çıktığı banka/nakit hesabı (isteğe bağlı, varsayılan "Belirtme")
  // düzeltmede sunucudaki bağ: paid_account_id (seçili gelir), paid_link "dokum" ise hesap değiştirilemez
  const sts = useQuery({ queryKey: ["statements"], queryFn: api.statements, enabled: p.type === "statement" });
  const st = sts.data?.find((x) => x.id === p.id);
  const savedAcc = st?.paid_account_id ?? null;
  const fromDokum = st?.paid_link === "dokum";
  const [stPick, setStPick] = useState<number | null | undefined>(undefined); // undefined: kullanıcı değiştirmedi
  const stAcc = stPick !== undefined ? stPick : savedAcc;
  const amt = parseAmount(amount);
  const valid = Number.isFinite(amt) && amt > 0;
  const partial = valid && amt + 0.01 < full;
  const tooMuch = valid && debt && amt > left + 0.01;

  const save = useSave(async () => {
    if (!valid) throw new Error("Ödenen tutarı yaz");
    if (date > todayISO()) throw new Error("Ödeme tarihi ileri bir gün olamaz");
    // hesap değişmediyse anahtar gönderilmez (bağlı hesap korunur, tutar/tarih güncellenir); "Hesaptan değil" → null
    if (p.type === "statement") return api.payStatement(p.id!, amt, date, fromDokum || stPick === undefined || stPick === savedAcc ? undefined : stPick);
    if (debt) return api.payDebt(p.id!, partial ? amt : null, date, debtAcc);
    return api.payBill(p.id!, amt, date, payment);
  }, (r) => {
    const linked = !!r && typeof r === "object" && "linked" in r && r.linked;
    const warning = r && typeof r === "object" && "warning" in r && typeof r.warning === "string" ? r.warning : null;
    onSaved?.(`${partial ? `${p.title}: ${tl(amt)} ödendi` : `${p.title} ödendi olarak işaretlendi`}${linked ? " · dökümdeki ödemeyle eşleşti" : ""}`);
    if (warning) setWarn(warning); // kayıt yapıldı; uyarı okunsun diye pencere açık kalır
    else onClose();
  });
  const undo = useSave(async () => (p.type === "statement" ? api.unpayStatement(p.id!)
    : debt ? api.unpayDebt(p.id!) : api.unpayBill(p.id!)),
    () => { onSaved?.("Ödeme geri alındı"); onClose(); });
  const chip = "btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700";

  return (
    <Modal title={paid ? "Ödemeyi düzelt" : "Ödedim"} onClose={onClose} footer={
      <>
        {(paid || (p.paid_amount ?? 0) > 0) && (
          <button className="btn-danger" disabled={undo.isPending} onClick={() => window.confirm(p.type === "statement"
            ? "Ödeme geri alınsın mı? Bir hesaptan ödendiğini seçtiysen o kart ödemesi kaydı silinir ve hesabın bakiyesi geri gelir; ödeme banka dökümünden geldiyse yalnız ekstreyle bağı kopar, döküm satırı kalır."
            : "Ödeme geri alınsın mı?") && undo.mutate(undefined)}>Ödemeyi geri al</button>
        )}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending || !valid || !!warn} onClick={() => save.mutate(undefined)}>
          {save.isPending ? "Kaydediliyor…" : "Kaydet"}</button>
      </>
    }>
      <div className="space-y-3">
        <div className="rounded-lg bg-stone-100 p-3 text-sm dark:bg-stone-800">
          <div className="flex justify-between gap-2"><b>{p.title}</b><b className="shrink-0 tabular-nums">{tl(p.amount)}</b></div>
          <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">{p.subtitle} · son ödeme {dateTR(p.due_date)}</div>
          {!paid && (p.paid_amount ?? 0) > 0 && (
            <div className="mt-1 text-xs text-stone-600 dark:text-stone-300">Ödenen {tl(p.paid_amount!)} · kalan {tl(left)}</div>
          )}
        </div>
        <div>
          <label className="label" htmlFor="p-amt">{debt ? "Bu ödemenin tutarı (₺)" : "Ödenen tutar (₺)"}</label>
          <input id="p-amt" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Hazır tutarlar">
            <button type="button" className={chip} aria-pressed={Math.abs(amt - full) < 0.01} onClick={() => setAmount(String(full))}>
              {debt ? `Kalanın tamamı (${tl(left)})` : "Tamamı"}</button>
            {p.min_payment != null && p.min_payment < p.amount && (
              <button type="button" className={chip} aria-pressed={Math.abs(amt - p.min_payment) < 0.01}
                onClick={() => setAmount(String(p.min_payment))}>Asgari ({tl(p.min_payment)})</button>
            )}
          </div>
          {partial && p.type === "statement" && (
            <p className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              Kısmi ödeme: kalan {tl(p.amount - amt)} bir sonraki ekstreye faiziyle (aylık ~%3,75 + vergiler) devreder.
            </p>
          )}
          {partial && p.type === "bill" && (
            <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">Kısmi ödeme: kalan {tl(p.amount - amt)} ödenmemiş görünür.</p>
          )}
          {partial && debt && <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">Kısmi ödeme: kalan {tl(left - amt)} açık kalır.</p>}
          {tooMuch && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">Kalan borçtan fazla; kalanın tamamı kapatılır.</p>}
        </div>
        <div>
          <label className="label" htmlFor="p-date">Ödeme tarihi</label>
          <input id="p-date" type="date" className="input" value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} />
        </div>
        {debt && !paid && (
          <AccountSelect id="p-acc" label="Hangi hesaptan ödedin" value={debtAcc} onChange={setAcc} />
        )}
        {p.type === "bill" && (
          <div>
            <span className="label">Nasıl ödendi</span>
            <Segmented value={payment} onChange={setPayment} options={[
              { value: "havale", label: "Havale / otomatik" },
              { value: "kredi_karti", label: "Kredi kartı" },
              { value: "nakit", label: "Nakit" },
            ]} />
            <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {payment === "kredi_karti"
                ? "Gider olarak eklenir; ekstre gelince karttaki işlemle eşleşir, iki kez sayılmaz."
                : "Bu ayın giderlerine eklenir."}
            </p>
          </div>
        )}
        {p.type === "statement" && (<>
          <div>
            <AccountSelect id="p-stacc" label="Hangi hesaptan? (isteğe bağlı)" value={stAcc} onChange={setStPick} kinds={["banka", "nakit"]}
              noneLabel="Hesaptan değil" disabled={fromDokum || (paid && sts.isLoading)} />
            <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {fromDokum ? "Bu ödeme banka dökümünden geldi; hesap hareketi değişmez."
                : "Seçersen bakiyen hemen düşer; banka dökümü gelince aynı ödeme otomatik eşleşir, iki kez sayılmaz. Gider sayılmaz."}
            </p>
          </div>
        </>)}
        {warn && (
          <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" role="alert">
            <b>Kaydedildi, ama:</b> {warn}
            <div className="mt-2"><button className="btn-primary px-3 py-1 text-xs" onClick={onClose}>Tamam</button></div>
          </div>
        )}
        {(save.error || undo.error) && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{((save.error || undo.error) as Error).message}</p>}
      </div>
    </Modal>
  );
}
