import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, ArrowDownRight, Ban, Plus, RotateCcw, Target, Trash2 } from "lucide-react";
import { useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import PriceWatch from "../components/PriceWatch";
import Targets from "../components/Targets";
import { useMeta, useSave } from "../components/hooks";
import Modal from "../components/Modal";
import Segmented from "../components/Segmented";
import { addMonths, cx, dateTR, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";

interface Sub {
  merchant: string;
  category: string;
  monthly: number;
  last_seen: string;
  months: number;
  fx: string | null;
  status: "aktif" | "iptal" | "dusuruldu";
  cancelled_at: string | null;
  charged_after_cancel: number;
  new_amount: number | null;
  saving: number;
  overcharged: number;
}
interface Goal {
  id: number;
  kind: "category" | "merchant";
  name: string;
  baseline: number;
  target: number;
  start_month: string;
  note: string | null;
  active: number;
  monthly_saving: number;
  months: { month: string; spent: number; status: "tuttu" | "tutmadi" | "devam"; saved: number }[];
}
interface SavingsData {
  subscriptions: Sub[];
  goals: Goal[];
  monthly_saving: number;
  cancelled_monthly: number;
  goal_monthly: number;
  last_month_spend: { by_category: Record<string, number>; by_merchant: Record<string, number> };
  reference_month: string;
}

const j = async (method: string, url: string, body?: unknown) => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
  return d;
};
// İşyeri adları normalize edilmiş ASCII (İ/ı → I); Türkçe küçültme "ANTHROPIC"i "anthropıc" yapar
export const title = (s: string) => s.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());

function GoalForm({ data, onClose }: { data: SavingsData; onClose: () => void }) {
  const ui = useUI();
  const meta = useMeta();
  const [kind, setKind] = useState<"category" | "merchant">("category");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [start, setStart] = useState(addMonths(ui.month, 1));
  const [note, setNote] = useState("");
  const src = kind === "category" ? data.last_month_spend.by_category : data.last_month_spend.by_merchant;
  const baseline = src[name] ?? 0;
  const options = kind === "category"
    ? (meta.data?.categories ?? []).filter((c) => c !== "Kategorisiz")
    : Object.keys(data.last_month_spend.by_merchant);
  const save = useSave(() => j("POST", "/api/goals", {
    kind, name, baseline, target: parseAmount(target), start_month: start, note: note || null,
  }), () => { ui.toast("Hedef eklendi, planlama güncellendi"); onClose(); });
  const t = parseAmount(target);

  return (
    <Modal title="Kısma hedefi" onClose={onClose} footer={
      <>
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={!name || !target || save.isPending}
          onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        <Segmented value={kind} onChange={(k) => { setKind(k); setName(""); }}
          options={[{ value: "category", label: "Kategori" }, { value: "merchant", label: "İşyeri" }]} />
        <div>
          <label className="label" htmlFor="g-name">{kind === "category" ? "Kategori" : "İşyeri"}</label>
          <CategoryPicker id="g-name" value={name} onChange={setName} options={options}
            placeholder={kind === "category" ? "ör. Yemek Siparişi" : "ör. UBER EATS YEMEK"} />
          {name && <p className="mt-1 text-xs text-stone-500">{monthLabel(data.reference_month)}: <b>{tl(baseline)}</b></p>}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="g-target">Aylık hedef (₺)</label>
            <input id="g-target" className="input text-right" inputMode="decimal" value={target}
              placeholder={baseline ? String(Math.round(baseline * 0.6 / 100) * 100) : ""} onChange={(e) => setTarget(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="g-start">Başlangıç</label>
            <input id="g-start" type="month" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
        </div>
        {name && t >= 0 && baseline > t && (
          <p className="rounded-lg bg-green-50 p-2 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            Tutarsa aylık <b>{tl(baseline - t)}</b>, yıllık <b>{tl((baseline - t) * 12)}</b> tasarruf.
          </p>
        )}
        <div>
          <label className="label" htmlFor="g-note">Neden / nasıl</label>
          <input id="g-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="ör. haftada 1 sipariş" />
        </div>
      </div>
    </Modal>
  );
}

export function DowngradeForm({ sub, onClose }: { sub: { merchant: string; monthly: number }; onClose: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState("");
  const n = parseAmount(amount);
  const save = useSave(() => j("PUT", "/api/subscriptions", { merchant: sub.merchant, status: "dusuruldu", new_amount: n }),
    () => { ui.toast("Paket değişikliği kaydedildi"); onClose(); });
  return (
    <Modal title={`${title(sub.merchant)} · paket değiştir`} onClose={onClose} footer={
      <>
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={!amount || !(n >= 0) || save.isPending}
          onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        <p className="text-sm">Şu an aylık <b>{tl(sub.monthly)}</b>. Yeni paketin aylık tutarı:</p>
        <input className="input text-right" inputMode="decimal" autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="ör. 700" />
        {n >= 0 && amount && n < sub.monthly && (
          <p className="rounded-lg bg-green-50 p-2 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            Aylık <b>{tl(sub.monthly - n)}</b>, yıllık <b>{tl((sub.monthly - n) * 12)}</b> tasarruf. Dövizli aboneliklerde tutar kurla biraz oynar.
          </p>
        )}
        <p className="text-xs text-stone-500">Sonraki ekstrede eski tutar çekilmeye devam ederse uyarı gelir.</p>
      </div>
    </Modal>
  );
}

export default function Savings() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["savings"], queryFn: () => fetch("/api/savings").then((r) => r.json() as Promise<SavingsData>) });
  const [adding, setAdding] = useState(false);
  const [downgrade, setDowngrade] = useState<Sub | null>(null);
  const setSub = useSave((b: { merchant: string; status: string }) => j("PUT", "/api/subscriptions", b),
    () => ui.toast("Abonelik güncellendi, planlama yeniden hesaplandı"));
  const patchGoal = useSave((b: { id: number; active: boolean }) => j("PATCH", `/api/goals/${b.id}`, { active: b.active }));
  const delGoal = useSave((id: number) => j("DELETE", `/api/goals/${id}`), () => ui.toast("Hedef silindi"));
  const d = q.data;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Tasarruf</h1>

      <Targets />

      {d && (
        <div className="card flex items-center gap-4 p-4">
          <Target className="shrink-0 text-green-600" />
          <div className="flex-1">
            <div className="text-xs text-stone-500">İptaller, paket düşürmeler ve hedefler tutarsa</div>
            <div className="text-xl font-semibold text-green-700 tabular-nums dark:text-green-400">{tl(d.monthly_saving)} / ay</div>
            <div className="text-xs text-stone-500">yıllık {tl(d.monthly_saving * 12)} · abonelik {tl(d.cancelled_monthly)} · hedef {tl(d.goal_monthly)}</div>
          </div>
        </div>
      )}

      <section className="card overflow-hidden">
        <div className="border-b border-stone-100 px-4 py-2.5 dark:border-stone-800">
          <h2 className="font-semibold">Abonelikler</h2>
          <p className="text-xs text-stone-500">İptal ettiğini işaretle: planlamadan düşer; sonraki ekstrede yine çekilirse uyarı gelir.</p>
        </div>
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {d?.subscriptions.map((s) => (
            <li key={s.merchant} className={cx("px-4 py-2.5", s.status === "iptal" && !s.charged_after_cancel && "opacity-60")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className={cx("font-medium", s.status === "iptal" && "line-through")}>{title(s.merchant)}</div>
                  <div className="text-xs text-stone-500">
                    {s.category} · son {dateTR(s.last_seen)}{s.fx && ` · ${s.fx}`}
                    {s.status !== "aktif" && s.cancelled_at && ` · ${s.status === "iptal" ? "iptal" : "değişiklik"}: ${dateTR(s.cancelled_at)}`}
                  </div>
                </div>
                <span className="shrink-0 text-right font-semibold tabular-nums">
                  {s.status === "dusuruldu" && s.new_amount != null
                    ? <><span className="text-xs font-normal text-stone-400 line-through">{tl(s.monthly)}</span> {tl(s.new_amount)}</>
                    : tl(s.monthly)}
                  <span className="text-xs font-normal text-stone-500"> /ay</span>
                </span>
              </div>
              {s.status === "dusuruldu" && (
                <div className="mt-0.5 text-xs text-green-700 dark:text-green-400">Paket düşürüldü · aylık {tl(s.saving)} tasarruf</div>
              )}
              {s.overcharged > 0 && (
                <div className="mt-1 flex items-center gap-1 text-xs font-medium text-amber-600">
                  <AlertOctagon size={13} /> Paket düşmemiş görünüyor: {tl(s.overcharged)} fazla çekildi
                </div>
              )}
              {s.charged_after_cancel > 0 && (
                <div className="mt-1 flex items-center gap-1 text-xs font-medium text-red-600">
                  <AlertOctagon size={13} /> İptalden sonra {tl(s.charged_after_cancel)} çekildi
                </div>
              )}
              <div className="mt-1.5 flex gap-2">
                {s.status === "aktif" ? (
                  <>
                    <button className="btn-danger px-2 py-1 text-xs" onClick={() => setSub.mutate({ merchant: s.merchant, status: "iptal" })}><Ban size={14} /> İptal ettim</button>
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setDowngrade(s)}><ArrowDownRight size={14} /> Paket değiştirdim</button>
                  </>
                ) : <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setSub.mutate({ merchant: s.merchant, status: "aktif" })}><RotateCcw size={14} /> Geri al</button>}
              </div>
            </li>
          ))}
          {d && !d.subscriptions.length && <li className="px-4 py-4 text-sm text-stone-500">Abonelik bulunamadı.</li>}
        </ul>
      </section>

      <section className="card overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-stone-100 px-4 py-2.5 dark:border-stone-800">
          <div>
            <h2 className="font-semibold">Kısma hedefleri</h2>
            <p className="text-xs text-stone-500">Planlama hedefe göre hesaplanır; ay sonunda tuttu mu görürsün.</p>
          </div>
          <button className="btn-primary shrink-0 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Hedef</button>
        </div>
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {d?.goals.map((g) => (
            <li key={g.id} className={cx("px-4 py-3", !g.active && "opacity-50")}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium">{g.kind === "merchant" ? title(g.name) : g.name}</div>
                  <div className="text-xs text-stone-500">
                    {tl(g.baseline)} → <b>{tl(g.target)}</b> · başlangıç {monthLabel(g.start_month, true)}{g.note && ` · ${g.note}`}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => patchGoal.mutate({ id: g.id, active: !g.active })}>
                    {g.active ? "Durdur" : "Devam"}
                  </button>
                  <button className="btn-danger p-1.5" aria-label="Sil" onClick={() => confirm("Hedef silinsin mi?") && delGoal.mutate(g.id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {g.months.map((m) => (
                  <span key={m.month} title={`${tl(m.spent)} harcandı`}
                    className={cx("rounded-full px-2 py-0.5 text-xs font-medium",
                      m.status === "tuttu" && "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
                      m.status === "tutmadi" && "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
                      m.status === "devam" && "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300")}>
                    {m.status === "tuttu" ? "✓" : m.status === "tutmadi" ? "✗" : "…"} {monthLabel(m.month, true)} · {tl(m.spent)}
                  </span>
                ))}
                {!g.months.length && <span className="text-xs text-stone-500">{monthLabel(g.start_month)} başlayınca takip edilecek</span>}
              </div>
            </li>
          ))}
          {d && !d.goals.length && (
            <li className="px-4 py-4 text-sm text-stone-500">ör. "Yemek siparişi 2.645 ₺ → 1.500 ₺" — planlama buna göre güncellenir.</li>
          )}
        </ul>
      </section>

      <PriceWatch />

      {adding && d && <GoalForm data={d} onClose={() => setAdding(false)} />}
      {downgrade && <DowngradeForm sub={downgrade} onClose={() => setDowngrade(null)} />}
    </div>
  );
}
