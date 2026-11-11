import { CalendarClock, Pencil, X } from "lucide-react";
import { useState } from "react";
import { call } from "../lib/apiError";
import { ask } from "../lib/ask";
import type { DebtSchedule } from "../lib/debtPlan";
import { ALACAKLI } from "../lib/borcKosul";
import { oneriTaksit, planFarki, planFarkMetni, planKalani, planOncesiOdenen } from "../lib/odemePlani";
import { dateTR, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { isLimited, useBook, useSave } from "./hooks";
import type { Debt } from "./Debts";

/** Kime: Kişi, Vergi / SGK, Banka, Mağaza / senet, Diğer (sunucu app/debt_schedule.CREDITORS; lib/borcKosul.ts) */
export const CREDITORS = ALACAKLI;

const thisMonth = () => new Date().toISOString().slice(0, 7);
const tl0 = (n: number) => tl(n).replace(/,00$/, "");
const putSchedule = (id: number, body: unknown) => call<{ ok: true; debt: Debt }>("PUT", `/api/debts/${id}/schedule`, body);
const planBody = (s: DebtSchedule) => ({ plan_installment: s.installment, plan_count: s.count, plan_start: s.start_month,
  plan_day: s.day, creditor_type: s.creditor_type, plan_interest: s.interest });

/** Borç detayında "Ödeme planı" bölümü (yetenek debt_schedule_edit): plan borcun alt kaydıdır. Yoksa "Ödeme planı ekle";
 *  varsa özet + Düzenle + Planı kaldır (borç kalır, diğer borç olur). Yalnız açık TL "borcum"da. Mobil
 *  components/borc/OdemePlani.tsx aynı sözler. */
export default function DebtScheduleSection({ d }: { d: Debt }) {
  const ui = useUI();
  const limited = isLimited(useBook().data); // ortak defterde sahip ya da tam yetkili üye ekler
  const [editing, setEditing] = useState(false);
  const s = d.schedule;
  const off = useSave(() => call("DELETE", `/api/debts/${d.id}/schedule`), () => {
    const prev = s;
    ui.toast("Ödeme planı kaldırıldı; borç diğer borç olarak duruyor", prev ? { undo: () => putSchedule(d.id, planBody(prev)) } : undefined);
  });
  if (d.direction !== "borc" || d.asset !== "TRY" || d.settled_at) return null;
  return (
    <section className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700" data-testid="odeme-plani">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold"><CalendarClock size={15} aria-hidden /> Ödeme planı</h3>
      {editing ? <ScheduleForm d={d} onDone={() => setEditing(false)} /> : s ? (<>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs" data-testid="odeme-plani-ozet">
          <dt className="text-stone-500">Kime</dt><dd>{s.creditor_label}</dd>
          <dt className="text-stone-500">Aylık taksit</dt><dd className="tabular-nums">{tl0(s.installment)} × {s.count}</dd>
          <dt className="text-stone-500">İlk taksit</dt><dd>{monthLabel(s.start_month)}</dd>
          <dt className="text-stone-500">Ayın kaçında</dt><dd>{s.day}</dd>
          <dt className="text-stone-500">Sıradaki taksit</dt>
          <dd className={s.overdue ? "font-medium text-red-600 dark:text-red-400" : undefined}>
            {s.next ? `${s.next.no}. taksit · ${dateTR(s.next.date)} · ${tl0(s.next.left)}` : "Taksitlerin hepsi ödendi"}</dd>
          <dt className="text-stone-500">Ödenen / kalan</dt>
          <dd>{s.paid_count} taksit ödendi · {s.left_count ?? s.count - s.paid_count} kalan</dd>
          {s.interest_label && (<><dt className="text-stone-500">Faiz / zam</dt><dd>{s.interest_label}</dd></>)}
        </dl>
        {!!s.paid_before && (
          <p className="text-xs text-stone-500 dark:text-stone-400">Plandan önce ödenen {tl0(s.paid_before)} korunur; taksitler kalan {tl0(s.total ?? d.remaining)} üzerinden sayılır.</p>
        )}
        {!limited && (
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setEditing(true)}><Pencil size={13} aria-hidden /> Düzenle</button>
            <button className="btn-ghost px-2 py-1 text-xs text-stone-500" disabled={off.isPending}
              onClick={() => void ask("Ödeme planı kaldırılsın mı?\n\nBorç silinmez: diğer borç olarak kalır; tutar ve ödemeler değişmez.",
                { confirm: "Planı kaldır", danger: false }).then((ok) => { if (ok) off.mutate(undefined); })}>
              <X size={13} aria-hidden /> Planı kaldır</button>
          </div>
        )}
      </>) : (<>
        <p className="text-xs text-stone-500 dark:text-stone-400">Bu borç için taksit anlaştıysan kaydet: Borç planı her ay önce taksiti ayırır, günü gelince hatırlatılır.</p>
        {!limited && <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setEditing(true)}>Ödeme planı ekle</button>}
      </>)}
    </section>
  );
}

/** Plan ekle / düzenle: PUT /api/debts/{id}/schedule. Borcun tutarı korunur; öneri kalan ÷ sayı. Toplam ≠ kalan ise
 *  yalnız bilgi verilir; istenirse borç tutarı taksit toplamına göre güncellenir. */
export function ScheduleForm({ d, onDone, creditor: fixedCreditor }: { d: Debt; onDone: () => void;
  /** borç formunda "Kime" zaten seçildi: burada ayrıca sorulmaz */ creditor?: DebtSchedule["creditor_type"] }) {
  const ui = useUI();
  const s0 = d.schedule;
  const [creditorSel, setCreditor] = useState<DebtSchedule["creditor_type"]>(s0?.creditor_type ?? d.creditor_type ?? "kisi");
  const creditor = fixedCreditor ?? creditorSel;
  const [inst, setInst] = useState(s0 ? String(s0.installment).replace(".", ",") : "");
  const [instTouched, setInstTouched] = useState(!!s0);
  const [count, setCount] = useState(s0 ? String(s0.count) : "");
  const [start, setStart] = useState(() => s0?.start_month ?? thisMonth());
  const [day, setDay] = useState(() => String(s0?.day ?? Number(new Date().toISOString().slice(8, 10))));
  const [interest, setInterest] = useState<string>(s0?.interest ?? "");
  const [setAmount, setSetAmount] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const kalan = planKalani(d.amount, d.payments, start);
  const once = planOncesiOdenen(d.payments, start);
  const i = parseAmount(inst);
  const n = Number(count);
  const fark = planFarki(kalan, i, n);
  const onCount = (v: string) => {
    const c = v.replace(/\D/g, "").slice(0, 3);
    setCount(c);
    if (!instTouched && Number(c) > 0 && kalan > 0) setInst(String(oneriTaksit(kalan, Number(c))));
  };
  const save = useSave(() => {
    const dd = Number(day);
    if (!(i > 0)) throw new Error("Aylık taksit tutarını yaz");
    if (!(n >= 1)) throw new Error("Taksit sayısını yaz");
    if (!(dd >= 1 && dd <= 31)) throw new Error("Ödeme günü 1 ile 31 arasında olmalı");
    return putSchedule(d.id, { plan_installment: i, plan_count: Math.round(n), plan_start: start, plan_day: dd,
      creditor_type: creditor, plan_interest: interest || null,
      ...(setAmount && fark && fark.fark > 0 ? { amount: Math.round((once + fark.toplam) * 100) / 100 } : {}) });
  }, () => { ui.toast("Ödeme planı kaydedildi"); onDone(); });
  return (
    <div className="space-y-2" data-testid="odeme-plani-form">
      <p className="text-xs text-stone-500 dark:text-stone-400">Anlaştığın taksitleri kaydet. Kalan borç {tl0(kalan)}{once > 0 ? ` (plandan önce ödenen ${tl0(once)} korunur)` : ""}.</p>
      <div className="grid grid-cols-2 gap-2">
        {!fixedCreditor && <label className="text-xs">
          <span className="label">Kime</span>
          <select className="input" value={creditor} onChange={(e) => setCreditor(e.target.value as DebtSchedule["creditor_type"])}>
            {CREDITORS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
          </select>
        </label>}
        <label className="text-xs">
          <span className="label">Taksit sayısı</span>
          <input className="input text-right" inputMode="numeric" placeholder="ör. 6" aria-label="Taksit sayısı" value={count} onChange={(e) => onCount(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="label">Aylık taksit</span>
          <input className="input text-right" inputMode="decimal" placeholder="ör. 2.000" aria-label="Aylık taksit" value={inst}
            onChange={(e) => { setInst(e.target.value); setInstTouched(true); }} />
        </label>
        <label className="text-xs">
          <span className="label">Ayın kaçında</span>
          <input className="input text-right" inputMode="numeric" aria-label="Ödeme günü" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, "").slice(0, 2))} />
        </label>
        {/* ay seçici dar ekranda yarım sütuna sığmıyor ("Kasım 20…"): telefonda tam satır */}
        <label className="col-span-2 text-xs sm:col-span-1">
          <span className="label">İlk taksit ayı</span>
          <input type="month" className="input" aria-label="İlk taksit ayı" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="col-span-2 text-xs sm:col-span-1">
          <span className="label">Faiz / gecikme zammı (yalnız kayıt)</span>
          <select className="input" value={interest} onChange={(e) => setInterest(e.target.value)}>
            <option value="">Belirtme</option><option value="yok">Yok</option><option value="var">Var</option><option value="bilmiyorum">Bilmiyorum</option>
          </select>
        </label>
      </div>
      {!instTouched && n > 0 && kalan > 0 && (
        <p className="text-xs text-stone-500 dark:text-stone-400" data-testid="odeme-plani-oneri">Öneri: kalan {tl0(kalan)} ÷ {n} ≈ {tl0(oneriTaksit(kalan, n))}. Değiştirebilirsin.</p>
      )}
      {fark && (
        <div className="space-y-1 rounded-md bg-stone-50 p-2 text-xs text-stone-600 dark:bg-stone-800/50 dark:text-stone-300" data-testid="odeme-plani-fark">
          <p>{planFarkMetni(fark, tl0)}</p>
          {fark.fark > 0 && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={setAmount} onChange={(e) => setSetAmount(e.target.checked)} />
              Borç tutarını taksit toplamına göre güncelle ({tl0(once + fark.toplam)})
            </label>
          )}
        </div>
      )}
      {err && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{err}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={save.isPending}
          onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>Kaydet</button>
        <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
      </div>
    </div>
  );
}
