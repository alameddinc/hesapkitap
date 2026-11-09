import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { useState } from "react";
import { api, type Payable, type Payment } from "../api";
import { cx, dateTR, parseAmount, tl, todayISO } from "../format";
import AccountSelect from "./AccountSelect";
import type { Debt } from "./Debts";
import { useSave } from "./hooks";
import Modal from "./Modal";
import Segmented from "./Segmented";
import { useUI } from "../ui";
import { ekstreSecenek, kalanNotu, secimOf, type Secim } from "../lib/odeme";

/** "Ödedim": kart ekstresi, fatura ya da vadeli borç ödemesini işaretle (tamamı / asgari / kısmi, tarih;
 *  faturada nasıl ödendiği, borçta hangi hesaptan). Mobil: components/planlama/OdemeSheet.tsx PaySheet. */
export default function PayForm({ p, onClose, onSaved }: { p: Payable; onClose: () => void; onSaved?: (m: string) => void }) {
  const ui = useUI();
  const paid = p.status === "odendi";
  // ilk ödeme mi: geri alma (unpay) önceki duruma birebir döner → bildirimde "Geri al". Önceden ödeme varsa
  // (düzeltme, kısmi ödemenin devamı) unpay hepsini siler; o zaman Geri al sunulmaz, pencerede "Ödemeyi geri al" var.
  const fresh = !paid && !((p.paid_amount ?? 0) > 0);
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
  const debts = useQuery({ queryKey: ["debts"], queryFn: () => send<Debt[]>("/api/debts"), enabled: debt });
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
  // kart ekstresi: "Ne kadar ödedin?" Tamamı / Asgari / Başka tutar (mobil OdemeSheet ile aynı)
  const isSt = p.type === "statement";
  const sec = ekstreSecenek(p, paid);
  const [diger, setDiger] = useState(false);
  const secim = secimOf(valid ? amt : null, sec, diger);
  const secimYap = (s: Secim) => {
    setDiger(s === "diger");
    if (s === "tam") setAmount(String(sec.full));
    else if (s === "asgari" && sec.min != null) setAmount(String(sec.min));
    else { setAmount(""); document.getElementById("p-amt")?.focus(); }
  };
  const tooMuch = valid && debt && amt > left + 0.01;

  const unpay = () => (p.type === "statement" ? api.unpayStatement(p.id!) : debt ? api.unpayDebt(p.id!) : api.unpayBill(p.id!));
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
    const msg = `${partial ? `${p.title}: ${tl(amt)} ödendi${isSt ? ` · kalan ${tl(p.amount - amt)}` : ""}` : `${p.title} ödendi olarak işaretlendi`}${linked ? " · dökümdeki ödemeyle eşleşti" : ""}`;
    // borç: sunucu payment_id verdiyse yalnız bu ödeme geri alınır (öncekiler kalır); yoksa ilk ödemede tümü
    const pid = debt && r && typeof r === "object" && "payment_id" in r && typeof r.payment_id === "number" ? r.payment_id : null;
    if (pid != null) ui.toast(msg, { undo: () => api.undoDebtPayment(pid) });
    else if (fresh) ui.toast(msg, { undo: unpay });
    else onSaved?.(msg);
    if (warning) setWarn(warning); // kayıt yapıldı; uyarı okunsun diye pencere açık kalır
    else onClose();
  }, { inline: true });
  const undo = useSave(unpay,
    () => { onSaved?.("Ödeme geri alındı"); onClose(); }, { inline: true });
  const chip = "btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700";

  return (
    <Modal title={paid ? "Ödemeyi düzelt" : "Ödedim"} onClose={onClose} footer={
      <>
        {(paid || (p.paid_amount ?? 0) > 0) && (
          <button className="btn-danger" disabled={undo.isPending} onClick={() => void ask(p.type === "statement"
            ? "Ödeme geri alınsın mı? Bir hesaptan ödendiğini seçtiysen o kart ödemesi kaydı silinir ve hesabın bakiyesi geri gelir; ödeme banka dökümünden geldiyse yalnız ekstreyle bağı kopar, döküm satırı kalır."
            : "Ödeme geri alınsın mı?").then((ok) => { if (ok) undo.mutate(undefined); })}>Ödemeyi geri al</button>
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
            <div className="mt-1 text-xs text-stone-600 dark:text-stone-300">Şu ana kadar ödenen {tl(p.paid_amount!)} · <b className="text-amber-700 dark:text-amber-300">kalan {tl(left)}</b></div>
          )}
        </div>
        {isSt && (
          <div role="radiogroup" aria-label="Ne kadar ödedin?" className="divide-y divide-stone-100 overflow-hidden rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
            {([["tam", "Tamamı", (p.paid_amount ?? 0) > 0 && !paid ? `kalan ${tl(left)} da ödendi` : "borcun hepsi", sec.full],
              ...(sec.min != null ? [["asgari", "Asgari", "kalanına faiz işleyebilir", sec.min]] : []),
              ["diger", "Başka tutar", "ödediğin tutarı yaz", null]] as [Secim, string, string, number | null][]).map(([k, ad, alt, v]) => (
              <button key={k} type="button" role="radio" aria-checked={secim === k} onClick={() => secimYap(k)}
                className={cx("flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", secim === k && "bg-stone-50 dark:bg-stone-800/60")}>
                <span aria-hidden="true" className={cx("size-4 shrink-0 rounded-full", secim === k ? "border-[5px] border-stone-900 dark:border-stone-100" : "border-2 border-stone-400")} />
                <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{ad}</span><span className="block text-xs text-stone-500 dark:text-stone-400">{alt}</span></span>
                {v != null && <span className="shrink-0 text-sm font-semibold tabular-nums">{tl(v)}</span>}
              </button>
            ))}
          </div>
        )}
        <div>
          <label className="label" htmlFor="p-amt">{debt ? "Bu ödemenin tutarı (₺)" : isSt && (p.paid_amount ?? 0) > 0 && !paid ? "Toplam ödenen (₺)" : "Ödenen tutar (₺)"}</label>
          <input id="p-amt" className="input text-right" inputMode="decimal" value={amount}
            onChange={(e) => { setAmount(e.target.value); const v = parseAmount(e.target.value); setDiger(!(Number.isFinite(v) && (Math.abs(v - sec.full) < 0.01 || (sec.min != null && Math.abs(v - sec.min) < 0.01)))); }} />
          {!isSt && (
            <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Hazır tutarlar">
              <button type="button" className={chip} aria-pressed={Math.abs(amt - full) < 0.01} onClick={() => setAmount(String(full))}>
                {debt ? `Kalanın tamamı (${tl(left)})` : "Tamamı"}</button>
            </div>
          )}
          {partial && isSt && (
            <p role="note" className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              {kalanNotu(tl(p.amount - amt))}
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
