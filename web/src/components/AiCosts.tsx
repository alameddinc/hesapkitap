import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Coins, Save } from "lucide-react";
import { useState } from "react";
import type { AiCosts as Costs, AiCostUser, AiCostUsers } from "../api";
import { call } from "../lib/apiError";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

const KEY = ["admin-ai-costs"];
const UKEY = ["admin-ai-cost-users"];
const n = (x: number) => x.toLocaleString("tr-TR");
const usd = (x: number) => `$${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
const tl = (x: number) => `₺${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const num = (s: string) => Number(s.replace(",", "."));
const PLAN: Record<string, string> = { free: "Ücretsiz", premium: "Premium", aile: "Aile", self: "Kendi sunucusu" };

/** Yönetici: ayın yapay zekâ maliyeti (tür başına çağrı, okuma, ortalama token, tahmini maliyet), kullanıcı başı
 *  maliyet listesi (eşiği geçen kırmızı, satır başı "Sınırla"). Model fiyatları ($ / 1M token), dolar kuru ve kullanıcı
 *  başı ₺ eşiği ayarlanabilir. */
export default function AiCosts() {
  const ui = useUI();
  const qc = useQueryClient();
  const [month, setMonth] = useState<string | null>(null);
  const qs = month ? `?month=${month}` : "";
  const q = useQuery({ queryKey: [...KEY, month], queryFn: () => call<Costs>("GET", `/api/admin/ai-costs${qs}`) });
  const uq = useQuery({ queryKey: [...UKEY, month], queryFn: () => call<AiCostUsers>("GET", `/api/admin/ai-costs/users${qs}`) });
  const d = q.data;
  const [form, setForm] = useState<{ prices: Record<string, { input: string; output: string }>; fx: string; alert: string } | null>(null);
  const f = form ?? (d ? {
    prices: Object.fromEntries(Object.entries(d.prices).map(([m, p]) => [m, { input: String(p.input), output: String(p.output) }])),
    fx: String(d.usd_try), alert: String(d.alert_try),
  } : null);
  const save = useSave(() => call<Costs>("PUT", "/api/admin/ai-costs", {
    prices: Object.fromEntries(Object.entries(f!.prices).map(([m, p]) => [m, { input: num(p.input), output: num(p.output) }])),
    usd_try: num(f!.fx), alert_try: num(f!.alert),
  }), () => {
    setForm(null);
    void qc.invalidateQueries({ queryKey: KEY });
    void qc.invalidateQueries({ queryKey: UKEY });
    ui.toast("Fiyat, kur ve eşik kaydedildi");
  });
  const months = d?.months?.length ? d.months : d ? [d.month] : [];

  return (
    <section className="card space-y-3 p-4" aria-labelledby="ai-costs-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="ai-costs-h" className="flex items-center gap-2 font-semibold"><Coins size={18} aria-hidden="true" /> Yapay zekâ maliyeti</h2>
        {d && months.length > 1 && (
          <label className="text-xs text-stone-500">Ay{" "}
            <select className="input ml-1 py-1" value={month ?? d.month} onChange={(e) => setMonth(e.target.value)}>
              {months.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}
      </div>
      <SorguDurumu q={q} etiket="Yapay zekâ maliyetleri yükleniyor"><SatirIskelet n={3} ikon={false} kart={false} /></SorguDurumu>
      {d && f && <>
        {d.over_alert > 0 && (
          <p role="alert" className="flex items-start gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-200">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
            <span>{d.month === months[0] ? "Bu ay" : d.month} eşiği ({tl(d.alert_try)}) geçen <b>{n(d.over_alert)} hesap</b> var.</span>
          </p>
        )}
        <p className="text-sm">
          <b>{d.month}</b>: {n(d.calls)} çağrı · tahmini <b>{usd(d.cost_usd)}</b> ({tl(d.cost_try)})
        </p>
        {/* dar ekran: tür başına kart; geniş ekran: tablo */}
        <ul className="space-y-2 sm:hidden">
          {d.kinds.map((k) => (
            <li key={k.kind} className="rounded-lg bg-stone-50 p-2 text-sm dark:bg-stone-800/60">
              <div className="flex justify-between gap-2 font-medium"><span>{k.label}</span><span className="tabular-nums">{usd(k.cost_usd)}</span></div>
              <div className="text-xs text-stone-600 tabular-nums dark:text-stone-400">
                {n(k.calls)} çağrı{k.reads != null ? ` · ${n(k.reads)} okuma` : ""} · {tl(k.cost_try)}
              </div>
              <div className="text-xs text-stone-500 tabular-nums">
                Ortalama girdi {n(k.avg_input)} · çıktı {n(k.avg_output)} · düşünme {n(k.avg_thoughts)} token
              </div>
            </li>
          ))}
        </ul>
        <div className="hidden sm:block">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-stone-500">
                <th className="py-1 font-normal">Tür</th>
                <th className="px-2 py-1 text-right font-normal">Çağrı</th>
                <th className="px-2 py-1 text-right font-normal">Okuma</th>
                <th className="px-2 py-1 text-right font-normal">Ort. girdi</th>
                <th className="px-2 py-1 text-right font-normal">Ort. çıktı</th>
                <th className="px-2 py-1 text-right font-normal">Ort. düşünme</th>
                <th className="py-1 pl-2 text-right font-normal">Maliyet</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 tabular-nums dark:divide-stone-800">
              {d.kinds.map((k) => (
                <tr key={k.kind}>
                  <td className="py-1.5 pr-2">{k.label}</td>
                  <td className="px-2 text-right">{n(k.calls)}</td>
                  <td className="px-2 text-right">{k.reads == null ? "—" : n(k.reads)}</td>
                  <td className="px-2 text-right">{n(k.avg_input)}</td>
                  <td className="px-2 text-right">{n(k.avg_output)}</td>
                  <td className="px-2 text-right">{n(k.avg_thoughts)}</td>
                  <td className="py-1.5 pl-2 text-right">{usd(k.cost_usd)}<span className="block text-xs text-stone-500">{tl(k.cost_try)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-stone-500">
          Okuma: aylık haktan düşen belge / fiş sayısı. Çağrı: yapay zekâya giden istek (bir belge birden çok çağrı olabilir;
          yerel okuyucuyla okunan belge çağrı yapmaz). Belge içeriği kaydedilmez, yalnız token sayıları.
        </p>
        <UserCosts data={uq.data} loading={uq.isLoading} error={uq.isError ? (uq.error as Error).message : null} />
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
          {Object.entries(f.prices).map(([m, p]) => (
            <div key={m} className="flex flex-wrap items-end gap-2">
              <label className="text-xs text-stone-500">Girdi $ / 1M <span className="break-all">({m})</span>
                <input className="input mt-1 block w-28" inputMode="decimal" value={p.input}
                  onChange={(e) => setForm({ ...f, prices: { ...f.prices, [m]: { ...p, input: e.target.value } } })} />
              </label>
              <label className="text-xs text-stone-500">Çıktı $ / 1M
                <input className="input mt-1 block w-28" inputMode="decimal" value={p.output}
                  onChange={(e) => setForm({ ...f, prices: { ...f.prices, [m]: { ...p, output: e.target.value } } })} />
              </label>
            </div>
          ))}
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-stone-500">Dolar kuru (₺)
              <input className="input mt-1 block w-28" inputMode="decimal" value={f.fx} onChange={(e) => setForm({ ...f, fx: e.target.value })} />
            </label>
            <label className="text-xs text-stone-500">Kullanıcı eşiği (₺ / ay)
              <input className="input mt-1 block w-28" inputMode="decimal" value={f.alert} onChange={(e) => setForm({ ...f, alert: e.target.value })} />
            </label>
            <button className="btn-primary inline-flex items-center gap-1" disabled={!form || save.isPending}><Save size={16} aria-hidden="true" /> Kaydet</button>
          </div>
        </form>
        <p className="text-xs text-stone-500">
          Düşünme token'ı çıktı fiyatıyla hesaplanır. Tabloda olmayan model en yakın aileden (flash-lite / flash) fiyatlanır.
          Varsayılan fiyat Google faturasından ölçülmüştür; değişirse buradan güncelle.
        </p>
      </>}
    </section>
  );
}

/** Kullanıcı başı aylık maliyet: en pahalı üstte; eşiği geçen satır kırmızı. Satıra dokununca tam e-posta (yönetici
 *  kullanıcı listesindeki gibi) ve "Sınırla" formu açılır. */
function UserCosts({ data, loading, error }: { data?: AiCostUsers; loading: boolean; error: string | null }) {
  const [open, setOpen] = useState<number | null>(null);
  if (loading) return <p className="text-sm text-stone-500">Kullanıcılar yükleniyor…</p>;
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!data) return null;
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium">Kullanıcı başı maliyet</h3>
      {data.users.length ? (
        <ol className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {data.users.map((u) => (
            <UserRow key={u.user_id ?? "none"} u={u} open={open === u.user_id && u.user_id != null}
              onToggle={() => setOpen(open === u.user_id ? null : u.user_id)} />
          ))}
        </ol>
      ) : <p className="text-sm text-stone-500">Bu ay çağrı yok.</p>}
      <p className="mt-1 text-xs text-stone-500">Kırmızı: aylık tahmini maliyeti {tl(data.alert_try)} eşiğini geçen hesap.</p>
    </div>
  );
}

function UserRow({ u, open, onToggle }: { u: AiCostUser; open: boolean; onToggle: () => void }) {
  const r = u.restriction;
  const red = u.over ? "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-100" : "";
  return (
    <li className={`rounded px-1 py-1.5 ${red}`}>
      <button type="button" className="flex w-full flex-wrap items-baseline justify-between gap-x-3 text-left tabular-nums"
        onClick={onToggle} disabled={u.user_id == null} aria-expanded={u.user_id == null ? undefined : open}>
        <span className="min-w-0 break-all">
          {open && u.username ? `#${u.user_id} · ${u.username}` : u.user}
          {u.plan && <span className="ml-1 text-xs text-stone-500">· {PLAN[u.plan] ?? u.plan}</span>}
          {r && <span className="ml-1 inline-flex items-center gap-0.5 rounded bg-amber-100 px-1 text-xs text-amber-900 dark:bg-amber-900/50 dark:text-amber-100">
            <Ban size={11} aria-hidden="true" />{r.mode === "no_ai" ? "Yapay zekâ kapalı" : `Günde ${r.daily_cap}`}</span>}
        </span>
        <span className={u.over ? "font-semibold" : "text-stone-600 dark:text-stone-400"}>
          {n(u.calls)} çağrı{u.reads != null ? ` · ${n(u.reads)} okuma` : ""} · {tl(u.cost_try)}
        </span>
      </button>
      {open && u.user_id != null && <RestrictForm u={u} />}
    </li>
  );
}

function RestrictForm({ u }: { u: AiCostUser }) {
  const ui = useUI();
  const qc = useQueryClient();
  const r = u.restriction;
  const [mode, setMode] = useState<"no_ai" | "cap">(r?.mode ?? "cap");
  const [cap, setCap] = useState(String(r?.daily_cap ?? 5));
  const [note, setNote] = useState(r?.note ?? "");
  const done = (msg: string) => { void qc.invalidateQueries({ queryKey: UKEY }); ui.toast(msg); };
  const put = useSave(() => call("PUT", `/api/admin/ai-restrictions/${u.user_id}`, {
    mode, daily_cap: mode === "cap" || cap.trim() ? Number(cap) : null, note: note.trim() || null,
  }), () => done("Hesap sınırlandı"));
  const del = useSave(() => call("DELETE", `/api/admin/ai-restrictions/${u.user_id}`), () => done("Sınır kaldırıldı"));
  return (
    <form className="mt-2 space-y-2 rounded-lg bg-stone-50 p-2 text-xs text-stone-700 dark:bg-stone-800/60 dark:text-stone-300"
      onSubmit={(e) => { e.preventDefault(); put.mutate(undefined); }}>
      <fieldset className="flex flex-wrap gap-3">
        <legend className="mb-1 font-medium">Sınırla</legend>
        <label className="inline-flex items-center gap-1"><input type="radio" name={`m${u.user_id}`} checked={mode === "cap"} onChange={() => setMode("cap")} /> Günlük üst sınır</label>
        <label className="inline-flex items-center gap-1"><input type="radio" name={`m${u.user_id}`} checked={mode === "no_ai"} onChange={() => setMode("no_ai")} /> Yapay zekâ kapalı (yerel okuma açık)</label>
      </fieldset>
      <div className="flex flex-wrap items-end gap-2">
        <label>Günde en çok belge{mode === "no_ai" ? " (isteğe bağlı)" : ""}
          <input className="input mt-1 block w-24" inputMode="numeric" value={cap} onChange={(e) => setCap(e.target.value.replace(/\D/g, ""))} />
        </label>
        <label className="min-w-0 flex-1">Not (kullanıcı görmez)
          <input className="input mt-1 block w-full" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <p className="text-stone-500">Paketin hakkı daha düşükse o uygulanır. Kullanıcıya "okuma geçici olarak sınırlandı; destek için bize yaz" denir.</p>
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={put.isPending || (mode === "cap" && !cap)}>{r ? "Güncelle" : "Sınırla"}</button>
        {r && <button type="button" className="btn-ghost" disabled={del.isPending} onClick={() => del.mutate(undefined)}>Kaldır</button>}
      </div>
    </form>
  );
}
