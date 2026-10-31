import { useQuery } from "@tanstack/react-query";
import { detailText } from "../lib/apiError";
import { Plus, Trash2, TrendingUp } from "lucide-react";
import { useState } from "react";
import { addMonths, cx, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Segmented from "./Segmented";

const tl0 = (x: number) => tl(Math.round(x)).replace(",00", "");

interface Change {
  id: number;
  from_month: string;
  gross: number;
  reason: "zam" | "is_degisikligi";
  reset_cum: number;
  pay_day: number | null;
  employer: string | null;
}
interface Draft {
  from_month: string;
  gross: number;
  reason: "zam" | "is_degisikligi";
  reset_cum: boolean;
  pay_day: number | null;
  employer: string | null;
}
interface Schedule {
  params_year: number;
  annual_net: number;
  resets: number[];
  months: { month: number; gross: number; net: number; income_tax: number; bracket_rate: number | null }[];
}

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(detailText(j.detail, r.status));
  return j as T;
};

/** Brüt maaşta zam / iş değişikliği: o dönemden itibaren yeni brüt; geçmiş aylar eski brütle kalır. */
export default function SalaryChanges({ rid }: { rid: number }) {
  const ui = useUI();
  const [thisYear] = useState(() => new Date().getFullYear());
  const list = useQuery({ queryKey: ["salary-changes", rid], queryFn: () => json<Change[]>("GET", `/api/recurring/${rid}/salary-changes`) });
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<Draft["reason"]>("zam");
  const [from, setFrom] = useState(addMonths(ui.month, 1));
  const [gross, setGross] = useState("");
  const [cum, setCum] = useState<"devam" | "sifir" | "bilmiyorum">("bilmiyorum");
  const [payDay, setPayDay] = useState("");
  const [employer, setEmployer] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const g = parseAmount(gross);
  const draft: Draft | null = g > 0 && /^\d{4}-\d{2}$/.test(from) ? {
    from_month: from, gross: g, reason,
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
  const add = useSave(() => json("POST", `/api/recurring/${rid}/salary-changes`, draft), () => {
    ui.toast(`${monthLabel(from)} döneminden itibaren yeni brüt kaydedildi`);
    setOpen(false);
    setGross("");
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
      {(list.data ?? []).length === 0 && !open && (
        <p className="text-xs text-stone-500">Zam alırsan ya da iş değiştirirsen buradan ekle; geçmiş aylar eski brütle kalır.</p>
      )}
      <ul className="space-y-1 text-sm">
        {list.data?.map((c) => (
          <li key={c.id} className="flex items-center gap-2">
            <span className="flex-1">
              <b>{monthLabel(c.from_month)}</b> döneminden itibaren {tl0(c.gross)} brüt
              <span className="text-xs text-stone-500">
                {" · "}{c.reason === "zam" ? "zam" : "iş değişikliği"}
                {c.pay_day ? ` · maaş günü ${c.pay_day}` : ""}
                {c.reset_cum ? " · matrah sıfırdan" : ""}
                {c.employer ? ` · ${c.employer}` : ""}
              </span>
            </span>
            <button type="button" className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil"
              onClick={() => del.mutate(c.id)}><Trash2 size={14} /></button>
          </li>
        ))}
      </ul>

      {open && (
        <div className="space-y-2 border-t border-stone-100 pt-2 dark:border-stone-800">
          <Segmented value={reason} onChange={setReason}
            options={[{ value: "zam", label: "Zam" }, { value: "is_degisikligi", label: "İş değişikliği" }]} />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              <span className="label">Hangi dönemden itibaren</span>
              <input type="month" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="text-xs">
              <span className="label">Yeni brüt (₺)</span>
              <input className="input text-right" inputMode="decimal" value={gross} onChange={(e) => setGross(e.target.value)} />
            </label>
          </div>
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
