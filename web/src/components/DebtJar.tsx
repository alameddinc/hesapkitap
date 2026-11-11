// Borç planı ↔ kumbara (yetenek "debt_jar"; sunucu app/debt_plan.py). Borç kartında "Kumbara aç" / Premium önerisi /
// bağlı kumbara; "Bu ay ödenecekler"de "Kumbaraya ayır" ve "Kumbaradan öde". Bütün tutar ve metinler sunucudan; mobil
// components/borc/BorcKumbara.tsx aynı sözlerle.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Lock, PiggyBank } from "lucide-react";
import { useState } from "react";
import { send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { offerSummary, openToast, payAmountEditable, planTon, type JarOpenResult, type JarPayResult, type PlanJar, type PlanJarAction, type PlanJarPay,
  type PlanJarPlan, type PlanJarPut } from "../lib/borcKumbara";
import { cx, maskText, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import Modal from "./Modal";

const today = () => new Date().toISOString().slice(0, 10);
const trimTl = (x: number) => tl(x).replace(/,00(?=\D*$)/, "");

function useRefresh() {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ refetchType: "all" });
}

/** "Kumbaraya ayır": bu ayın birikimi tek dokunuşla (Geri al: hareket silinir). */
export function useJarPut() {
  const ui = useUI();
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (a: PlanJarAction) => send<{ id: number }>(`/api/jars/${a.jar_id}/moves`, { method: "POST",
      json: { amount: a.amount, direction: "yatir", move_date: today(), note: a.note } }),
    onSuccess: (r, a) => {
      refresh();
      ui.toast(`${trimTl(a.amount)} kumbaraya ayrıldı`, { undo: async () => { await send(`/api/jar-moves/${r.id}`, { method: "DELETE" }); refresh(); } });
    },
    onError: (e: Error) => ui.error(e.message || "Kaydedilemedi"),
  });
}

/** Kumbaradan öde penceresi: tutar (kredide bankanın kapatma tutarı yazılır), onay metni, Geri al. */
export function JarPayDialog({ pay, title, onClose }: { pay: PlanJarPay; title: string; onClose: () => void }) {
  const ui = useUI();
  const refresh = useRefresh();
  const edit = payAmountEditable(pay);
  const [text, setText] = useState(pay.amount.toLocaleString("tr-TR", { maximumFractionDigits: 2 }));
  const amount = edit ? parseAmount(text) : pay.amount;
  const m = useMutation({
    mutationFn: () => send<JarPayResult>(`/api/jars/${pay.jar_id}/pay-debt`, { method: "POST",
      json: { paid_at: today(), ...(edit ? { amount } : {}) } }),
    onSuccess: async (r) => {
      onClose();
      refresh();
      ui.toast(r.message, { undo: async () => { await send(`/api/debt-payments/${r.payment_id}`, { method: "DELETE" }); refresh(); } });
      if (r.ask && await ask(r.ask.text, { title: "Kumbarada para kaldı", confirm: r.ask.close, cancel: "Sonra karar veririm", danger: false })) {
        await send(`/api/jars/${pay.jar_id}`, { method: "PATCH", json: { closed: true } }).catch((e: Error) => ui.error(e.message));
        refresh();
      }
    },
    onError: (e: Error) => ui.error(e.message || "Kaydedilemedi"),
  });
  return (
    <Modal title={`${title} · ${pay.label.toLowerCase()}`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" data-testid="kumbaradan-ode-onay" disabled={!(amount > 0) || m.isPending}
        onClick={() => m.mutate()}>{m.isPending ? "Kaydediliyor…" : pay.label}</button>}>
      <div className="space-y-3 text-sm">
        <p>{maskText(pay.confirm)}</p>
        {edit ? (
          <label className="block text-xs">Bankaya ödediğin kapatma tutarı
            <input className="input mt-1 text-right text-lg" inputMode="decimal" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
          </label>
        ) : <p className="text-lg font-semibold tabular-nums">{tl(pay.amount)}</p>}
        <p className="text-xs text-stone-500">Kumbaradan düşen en çok {tl(pay.from_jar)}. Ödemeyi geri alırsan kumbaradaki para da geri gelir.</p>
      </div>
    </Modal>
  );
}

/** Borç kartındaki kumbara kutusu. */
export function JarBox({ jar, debtId, title }: { jar: PlanJar; debtId: number; title: string }) {
  const ui = useUI();
  const refresh = useRefresh();
  const [paying, setPaying] = useState(false);
  const [putting, setPutting] = useState(false);
  const open = useMutation({
    mutationFn: () => send<JarOpenResult>(`/api/debts/${debtId}/jar`, { method: "POST", json: {} }),
    onSuccess: (r) => {
      refresh();
      ui.toast(openToast(r),
        { undo: async () => { await send(`/api/jars/${r.id}`, { method: "DELETE" }); refresh(); } });
    },
    onError: (e: Error) => ui.error(e.message || "Kumbara açılamadı"),
  });
  if (jar.state === "locked") {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-stone-50 p-2.5 text-xs text-stone-600 dark:bg-stone-900 dark:text-stone-300" data-testid="kumbara-kilit">
        <Lock size={14} className="mt-0.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1"><span className="font-medium text-stone-800 dark:text-stone-100">{jar.label}</span>
          <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">{jar.badge}</span>
          <span className="block">{jar.text}</span></span>
      </div>
    );
  }
  if (jar.state === "offer") {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-violet-50 p-2.5 text-xs text-violet-950 dark:bg-violet-950/40 dark:text-violet-100" data-testid="kumbara-ac">
        <PiggyBank size={15} className="shrink-0" aria-hidden />
        <span className="min-w-0 flex-1 basis-56"><span className="block">{jar.text}</span>
          <span className="block font-medium tabular-nums">{maskText(`${jar.name} · ${offerSummary(jar, trimTl)}`)}</span></span>
        <button className="btn-primary px-3 py-1 text-sm" disabled={open.isPending} onClick={() => open.mutate()}
          aria-label={`${title}: ${jar.label}`}>{jar.label}</button>
      </div>
    );
  }
  const pct = jar.pct ?? 0;
  return (
    <div className="space-y-1.5 rounded-lg bg-violet-50 p-2.5 text-xs text-violet-950 dark:bg-violet-950/40 dark:text-violet-100" data-testid="kumbara-bagli">
      <div className="flex flex-wrap items-center gap-2">
        <PiggyBank size={15} className="shrink-0" aria-hidden />
        <button className="min-w-0 flex-1 basis-40 text-left font-medium underline-offset-2 hover:underline" onClick={() => ui.go("hesaplar")}>
          Kumbara: {jar.name}</button>
        <span className="font-semibold tabular-nums">{maskText(jar.text)}</span>
      </div>
      {jar.goal != null && (
        <div className="h-1.5 overflow-hidden rounded-full bg-violet-100 dark:bg-violet-900/60" role="progressbar" aria-label={`${jar.name} birikimi`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
          <div className="h-full rounded-full bg-violet-600" style={{ width: `${pct}%` }} />
        </div>
      )}
      {jar.plan && <JarPlanLine p={jar.plan} />}
      {jar.put && !jar.pay && (
        <div className="flex justify-end">
          <button className="btn-ghost border border-violet-300 px-2 py-1 text-sm text-violet-800 dark:border-violet-700 dark:text-violet-200"
            data-testid="kumbaraya-ayir-serbest" onClick={() => setPutting(true)}>{jar.put.label}</button>
        </div>
      )}
      {putting && jar.put && <JarPutDialog put={jar.put} title={jar.name} onClose={() => setPutting(false)} />}
      {jar.pay && (
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <span className="min-w-0 flex-1 basis-48">{maskText(jar.pay.text)}</span>
          <button className="btn-primary px-3 py-1 text-sm" data-testid="kumbaradan-ode" onClick={() => setPaying(true)}>{jar.pay.label}</button>
        </div>
      )}
      {paying && jar.pay && <JarPayDialog pay={jar.pay} title={title} onClose={() => setPaying(false)} />}
    </div>
  );
}

/** "Bu ay ödenecekler" satırında düğme: Kumbaraya ayır / Kumbaradan öde. */
export function JarRowButton({ kind, action, pay, title, small }: { kind: "ayir" | "ode"; action?: PlanJarAction; pay?: PlanJarPay;
  title: string; small?: boolean }) {
  const put = useJarPut();
  const [paying, setPaying] = useState(false);
  const cls = cx("btn-ghost border border-violet-300 text-violet-800 dark:border-violet-700 dark:text-violet-200", small ? "px-2 py-1 text-xs" : "px-2 py-1 text-sm");
  if (kind === "ayir" && action) {
    return <button className={cls} data-testid="kumbaraya-ayir" disabled={put.isPending} onClick={() => put.mutate(action)}
      aria-label={`${title}: ${action.label} ${trimTl(action.amount)}`}>{action.label}</button>;
  }
  if (kind === "ode" && pay) {
    return (
      <>
        <button className={cls} data-testid="kumbaradan-ode" onClick={() => setPaying(true)} aria-label={`${title}: ${pay.label}`}>{pay.label}</button>
        {paying && <JarPayDialog pay={pay} title={title} onClose={() => setPaying(false)} />}
      </>
    );
  }
  return null;
}

/** Planın takvimine göre kumbara durumu (rozet + satır + takvim); kumbara kartında da kullanılır. */
export function JarPlanLine({ p }: { p: PlanJarPlan }) {
  const iyi = planTon(p) === "iyi";
  return (
    <div className={cx("rounded-md px-2 py-1.5", iyi ? "bg-green-50 dark:bg-green-950/40" : "bg-stone-50 dark:bg-stone-900")} data-testid="kumbara-plan" data-status={p.status}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-semibold text-stone-900 dark:text-stone-100">
        {p.badge && <span className="rounded-md bg-green-100 px-1.5 py-0.5 text-xs font-bold text-green-800 dark:bg-green-900/60 dark:text-green-200">{p.badge}</span>}
        <span className="tabular-nums">{maskText(p.line)}</span></p>
      {p.detail && <p className="text-xs text-stone-600 tabular-nums dark:text-stone-400">{maskText(p.detail)}</p>}
    </div>
  );
}

/** "Kumbaraya ayır" (tutar sorulur): plan bu ay ayırmıyorsa da; bu ayın plan tutarı varsa önerilir. Geri al var. */
export function JarPutDialog({ put, title, onClose }: { put: PlanJarPut; title: string; onClose: () => void }) {
  const ui = useUI();
  const refresh = useRefresh();
  const [text, setText] = useState(put.amount ? put.amount.toLocaleString("tr-TR", { maximumFractionDigits: 2 }) : "");
  const amount = parseAmount(text);
  const m = useMutation({
    mutationFn: () => send<{ id: number }>(`/api/jars/${put.jar_id}/moves`, { method: "POST",
      json: { amount, direction: "yatir", move_date: today(), note: put.note } }),
    onSuccess: (r) => {
      onClose();
      refresh();
      ui.toast(`${trimTl(amount)} kumbaraya ayrıldı`, { undo: async () => { await send(`/api/jar-moves/${r.id}`, { method: "DELETE" }); refresh(); } });
    },
    onError: (e: Error) => ui.error(e.message || "Kaydedilemedi"),
  });
  return (
    <Modal title={`${title} · ${put.label.toLowerCase()}`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(amount > 0) || m.isPending} onClick={() => m.mutate()}>{put.label}</button>}>
      <div className="space-y-3 text-sm">
        <label className="block text-xs">Tutar
          <input className="input mt-1 text-right text-lg" inputMode="decimal" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <p className="text-xs text-stone-500">{put.amount ? "Önerilen tutar planın bu ayki birikimi." : "Plan bu ay bu borca birikim ayırmıyor; ayırdığın para kumbarada durur ve plan sonraki ayları buna göre azaltır."}
          {" "}Para hesabında kalır; harcanabilir tutardan düşer.</p>
      </div>
    </Modal>
  );
}
