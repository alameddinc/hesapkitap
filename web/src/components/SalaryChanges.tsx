import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { Plus, Trash2, TrendingUp } from "lucide-react";
import { useState } from "react";
import { addMonths, cx, maskText, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Segmented from "./Segmented";

const tl0 = (x: number) => tl(Math.round(x)).replace(",00", "");
/** 15 → "%15", 11.2 → "%11,2" (sunucu salary.pct_text ile aynı) */
const pctLabel = (p: number) => `%${Math.abs(p).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;

interface Change {
  id: number;
  from_month: string;
  gross: number;
  reason: "zam" | "is_degisikligi";
  reset_cum: number;
  pay_day: number | null;
  employer: string | null;
  note?: string | null;
  pct?: number | null;
  pct_basis?: "brut" | "net" | null;
}
/** Yeni maaş: gross | net | pct (+basis); sunucu brüte çevirir (app/salary.py resolve_draft) */
interface Draft {
  from_month: string;
  gross?: number;
  net?: number;
  pct?: number;
  basis: "brut" | "net";
  reason: "zam" | "is_degisikligi";
  reset_cum: boolean;
  pay_day: number | null;
  employer: string | null;
  note: string | null;
}
/** Mobil MaasGecmisi ile aynı: sunucuda ayrı tür yok, "Düzeltme" = reason "zam" + note "Düzeltme" (zam sayılmaz) */
const DUZELTME = "Düzeltme";
type Tur = "zam" | "is_degisikligi" | "duzeltme";
const turOf = (c: Change): Tur => (c.reason === "is_degisikligi" ? "is_degisikligi" : c.note === DUZELTME ? "duzeltme" : "zam");
const TUR_AD: Record<Tur, string> = { zam: "zam", is_degisikligi: "iş değişikliği", duzeltme: "düzeltme" };
interface Schedule {
  params_year: number;
  annual_net: number;
  resets: number[];
  months: { month: number; gross: number; net: number; income_tax: number; bracket_rate: number | null }[];
  draft_resolved?: { gross: number; text: string | null; advance_text: string | null } | null;
}
/** GET /api/recurring/drift?salary=1 — maaş önerisi (metin sunucudan; mobil Öneriler ile aynı) */
export interface SalaryDrift {
  type: "maas"; id: string; recurring_id: number; month: string; title: string; text: string;
  accept_label: string; dismiss_label: string;
}

/** Peşin maaş notu (mobil MaasGecmisi ile aynı metin) */
export const PESIN = "Maaş peşin ödeniyor: ayın 15'inde, sonraki bir ay için önden yatar. Zam ayın 1'inde başladığı için ilk zamlı maaşa 1–14 arası günlerin farkı eklenir (aylık farkın 14/30'u; ör. ocak ve temmuz).";

const json = <T,>(method: string, url: string, body?: unknown) => call<T>(method, url, body);

export const useSalaryDrift = () =>
  useQuery({ queryKey: ["recurring-drift", "salary"], queryFn: () => json<({ type?: string; recurring_id: number })[]>("GET", "/api/recurring/drift?salary=1") });

/** Yatan maaş planla tutmuyorsa: "Maaşın %11,2 arttı (… → …). Sabit kalemi güncelle?" · Güncelle / Bu ay farklıydı */
export function SalaryDriftCard({ d }: { d: SalaryDrift }) {
  const ui = useUI();
  const body = { recurring_id: d.recurring_id, month: d.month };
  const accept = useSave(() => json("POST", "/api/recurring/salary-drift/accept", body), () => ui.toast("Maaş güncellendi"));
  const dismiss = useSave(() => json("POST", "/api/recurring/salary-drift/dismiss", body));
  const busy = accept.isPending || dismiss.isPending;
  return (
    <div className="space-y-2 rounded-lg border border-green-200 bg-green-50 p-3 text-sm dark:border-green-900 dark:bg-green-950/40">
      <div className="font-medium">{maskText(d.title)}</div>
      <div className="text-xs text-stone-600 dark:text-stone-400">{maskText(d.text)}</div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={busy} onClick={() => accept.mutate(undefined)}>{d.accept_label}</button>
        <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} onClick={() => dismiss.mutate(undefined)}>{d.dismiss_label}</button>
      </div>
    </div>
  );
}

/** Brüt maaşta zam / iş değişikliği: o dönemden itibaren yeni brüt; geçmiş aylar eski brütle kalır. */
export default function SalaryChanges({ rid, advance = false }: { rid: number; advance?: boolean }) {
  const ui = useUI();
  const [thisYear] = useState(() => new Date().getFullYear());
  const list = useQuery({ queryKey: ["salary-changes", rid], queryFn: () => json<Change[]>("GET", `/api/recurring/${rid}/salary-changes`) });
  const drift = useSalaryDrift();
  const myDrift = (drift.data ?? []).find((d) => d.type === "maas" && d.recurring_id === rid) as SalaryDrift | undefined;
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<Tur>("zam");
  const [from, setFrom] = useState(addMonths(ui.month, 1));
  const [gross, setGross] = useState("");
  const [basis, setBasis] = useState<Draft["basis"]>("brut");
  const [mode, setMode] = useState<"tutar" | "yuzde">("tutar");
  const [cum, setCum] = useState<"devam" | "sifir" | "bilmiyorum">("bilmiyorum");
  const [payDay, setPayDay] = useState("");
  const [employer, setEmployer] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const pct = mode === "yuzde" && reason === "zam";
  const g = parseAmount(gross);
  const ok = pct ? g !== 0 && g > -50 && g <= 300 : g > 0;
  const draft: Draft | null = ok && /^\d{4}-\d{2}$/.test(from) ? {
    from_month: from, basis, ...(pct ? { pct: g } : basis === "brut" ? { gross: g } : { net: g }),
    reason: reason === "is_degisikligi" ? "is_degisikligi" : "zam", note: reason === "duzeltme" ? DUZELTME : null,
    reset_cum: reason === "is_degisikligi" && cum === "sifir",
    pay_day: reason === "is_degisikligi" && Number(payDay) >= 1 && Number(payDay) <= 31 ? Number(payDay) : null,
    employer: reason === "is_degisikligi" && employer.trim() ? employer.trim() : null,
  } : null;
  const year = Number(from.slice(0, 4)) || thisYear;
  const preview = useQuery({
    queryKey: ["payroll-plan", rid, year, draft],
    queryFn: () => json<Schedule>("POST", `/api/recurring/${rid}/payroll`, { year, draft }),
    enabled: open && !!draft,
  });
  const resolved = draft ? preview.data?.draft_resolved : null;
  const add = useSave(() => json("POST", `/api/recurring/${rid}/salary-changes`, draft), () => {
    ui.toast(`${monthLabel(from)} döneminden itibaren yeni brüt kaydedildi`);
    setOpen(false);
    setGross("");
    setMode("tutar");
  });
  const del = useSave((id: number) => json("DELETE", `/api/salary-changes/${id}`));

  const fromMonthNo = Number(from.slice(5, 7));
  return (
    <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium"><TrendingUp size={16} /> Maaş değişiklikleri</span>
        {!open && (
          <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setOpen(true)}>
            <Plus size={14} /> Zam / iş değişikliği
          </button>
        )}
      </div>
      {myDrift && <SalaryDriftCard d={myDrift} />}
      {(list.data ?? []).length === 0 && !open && (
        <p className="text-xs text-stone-500">Zam alırsan ya da iş değiştirirsen buradan ekle; geçmiş aylar eski brütle kalır.</p>
      )}
      {advance && !open && <p className="text-xs text-stone-500">{PESIN}</p>}
      <ul className="space-y-1 text-sm">
        {list.data?.map((c) => (
          <li key={c.id} className="flex items-center gap-2">
            <span className="flex-1">
              <b>{monthLabel(c.from_month)}</b> döneminden itibaren {tl0(c.gross)} brüt
              <span className="text-xs text-stone-500">
                {" · "}{c.pct != null ? `${pctLabel(c.pct)} ${c.pct_basis === "net" ? "net " : ""}` : ""}{TUR_AD[turOf(c)]}
                {c.pay_day ? ` · maaş günü ${c.pay_day}` : ""}
                {c.reset_cum ? " · matrah sıfırdan" : ""}
                {c.employer ? ` · ${c.employer}` : ""}
              </span>
            </span>
            <button type="button" className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil" disabled={del.isPending}
              onClick={() => void ask("Maaş değişikliği silinsin mi?\n\nO aydan sonraki aylar önceki brütle hesaplanır.").then((ok) => { if (ok) del.mutate(c.id); })}><Trash2 size={14} /></button>
          </li>
        ))}
      </ul>

      {open && (
        <div className="space-y-2 border-t border-stone-100 pt-2 dark:border-stone-800">
          <Segmented value={reason} onChange={setReason}
            options={[{ value: "zam", label: "Zam" }, { value: "is_degisikligi", label: "İş değişikliği" }, { value: "duzeltme", label: "Düzeltme" }]} />
          {reason === "duzeltme" && <p className="text-xs text-stone-500">Brütü yanlış girdiysen o aydan itibaren doğrusunu yaz; zam olarak sayılmaz.</p>}
          <div className="flex flex-wrap gap-2 [&>*]:min-w-[12rem] [&>*]:flex-1">
            <Segmented value={basis} onChange={(b) => { setBasis(b); if (!pct) setGross(""); }} label="Tutar türü"
              options={[{ value: "net", label: "Net yazacağım" }, { value: "brut", label: "Brüt yazacağım" }]} />
            {reason === "zam" && (
              <Segmented value={mode} onChange={(m) => { setMode(m); setGross(""); }} label="Tutar mı yüzde mi"
                options={[{ value: "tutar", label: "Tutar" }, { value: "yuzde", label: "%" }]} />
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              <span className="label">Hangi dönemden itibaren</span>
              <input type="month" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="text-xs">
              <span className="label">{pct ? "Zam (%)" : basis === "brut" ? "Yeni brüt (₺)" : "Yeni net (₺)"}</span>
              <div className="relative">
                {pct && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-400">%</span>}
                <input className="input text-right" inputMode="decimal" placeholder={pct ? "15" : ""} aria-label={pct ? "Zam yüzdesi" : undefined}
                  value={gross} onChange={(e) => setGross(e.target.value)} />
              </div>
            </label>
          </div>
          {pct && <p className="text-xs text-stone-600 dark:text-stone-400">
            {resolved?.text ? maskText(resolved.text) : `Zammı ${basis === "brut" ? "brüte" : "nete"} göre yüzde olarak yaz; ${monthLabel(from)} öncesindeki maaşın üstüne eklenir.`}</p>}
          {!pct && basis === "net" && resolved && (
            <p className="text-xs text-stone-500">≈ {tl0(resolved.gross)} brüt olarak kaydedilir. Net her ay vergi dilimine göre değişir; bordrodaki brütü biliyorsan onu yaz.</p>
          )}
          {reason === "zam" && advance && (
            <div className="rounded-lg bg-stone-100 p-2 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-400">
              {resolved?.advance_text && <p className="mb-1 font-medium text-stone-800 dark:text-stone-200">{maskText(resolved.advance_text)}</p>}
              <p>{PESIN}</p>
            </div>
          )}
          {reason === "is_degisikligi" && (
            <>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs">
                  <span className="label">Yeni maaş günü</span>
                  <input className="input" inputMode="numeric" placeholder="aynı" value={payDay} onChange={(e) => setPayDay(e.target.value)} />
                </label>
                <label className="text-xs">
                  <span className="label">İşveren (bankada görünen ad)</span>
                  <input className="input" placeholder="ör. ÖRNEK YAZILIM" value={employer} onChange={(e) => setEmployer(e.target.value)} />
                </label>
              </div>
              <div className="text-xs">
                <span className="label">Vergi matrahı yeni işte</span>
                <Segmented value={cum} onChange={setCum} options={[
                  { value: "devam", label: "Devam ediyor" }, { value: "sifir", label: "Sıfırdan" }, { value: "bilmiyorum", label: "Bilmiyorum" },
                ]} />
                <p className="mt-1 text-stone-500">
                  Yeni işveren önceki matrahı devralırsa dilim aynı kalır; sıfırdan başlatırsa birkaç ay net yükselir.
                  İlk bordroda "kümülatif matrah" satırına bak; bilmiyorsan "devam" varsayılır.
                </p>
              </div>
            </>
          )}
          {draft && preview.data && (
            <div className="max-h-48 overflow-y-auto rounded-lg border border-stone-200 text-xs dark:border-stone-700">
              <table className="w-full tabular-nums whitespace-nowrap [overflow-wrap:normal]">
                <thead className="sticky top-0 bg-stone-100 dark:bg-stone-800">
                  <tr className="text-left">
                    <th className="px-2 py-1 font-medium">{year}</th>
                    <th className="px-2 py-1 text-right font-medium">Brüt</th>
                    <th className="px-2 py-1 text-right font-medium">Net</th>
                    <th className="px-2 py-1 text-right font-medium">Dilim</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.months.map((m) => (
                    <tr key={m.month} className={cx("border-t border-stone-100 dark:border-stone-800",
                      m.month >= fromMonthNo ? "" : "text-stone-400")}>
                      <td className="px-2 py-1">
                        {monthLabel(`${year}-${String(m.month).padStart(2, "0")}`, true)}
                        {preview.data.resets.includes(m.month) && <span className="ml-1 text-amber-600">↺</span>}
                      </td>
                      <td className="px-2 py-1 text-right">{m.gross ? tl0(m.gross) : "—"}</td>
                      <td className="px-2 py-1 text-right font-semibold">{m.net ? tl0(m.net) : "—"}</td>
                      <td className="px-2 py-1 text-right">{m.bracket_rate ? `%${Math.round(m.bracket_rate * 100)}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="border-t border-stone-100 px-2 py-1 text-stone-500 dark:border-stone-800">
                Yıllık net {tl(preview.data.annual_net)}
                {preview.data.params_year !== year && ` · ${year} vergi parametreleri yok, ${preview.data.params_year} kullanıldı (yaklaşık)`}
              </p>
            </div>
          )}
          {preview.error && draft && <p className="text-xs text-red-600">{preview.error.message}</p>}
          {err && <p className="text-xs text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button type="button" className="btn-primary" disabled={!draft || add.isPending}
              onClick={() => { setErr(null); add.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>
              Kaydet
            </button>
            <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Vazgeç</button>
          </div>
        </div>
      )}
    </div>
  );
}
