import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Sparkles, Store, Users } from "lucide-react";
import { useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import MonthPicker from "../components/MonthPicker";
import { useMeta } from "../components/hooks";
import { ApiError, send } from "../lib/apiError";
import { cx, dayMonthTR, monthLabel, tl } from "../format";
import { useUI } from "../ui";

// Kategorisizleri düzenle (sunucu app/uncategorized.py; mobil src/screens/Kategorisiz.tsx ile aynı akış).
// Küme Özet'teki "N işlem kategorisiz" uyarısıyla aynı; işyeri/kişi grupları, ücretsiz öneri, Uygula + Geri al,
// "Bundan sonra hep böyle" (kural / kişi kategorisi), önerisi olmayan işyerleri için yapay zekâ (1 okuma hakkı).

type Kaynak = "gecmis" | "kural" | "bilinen" | "yz";
interface Oneri { category: string; source: Kaynak; confidence: number }
interface Grup {
  key: string; label: string; kind: "isyeri" | "kisi"; flow: "gelir" | "gider"; count: number; total: number;
  sample_ids: string[]; first_date: string; last_date: string; suggestion: Oneri | null;
}
interface Yanit { month: string | null; groups: Grup[]; count: number; ai: { available: boolean; left: number | null } }
interface Uygula { changed: number; also_changed: number; remembered: "kural" | "kisi" | null; undo_id: string | null }

const KAYNAK: Record<Kaynak, string> = {
  gecmis: "Daha önce böyle seçmiştin", kural: "Kategori kuralından", bilinen: "Kart ekstresindeki sektörden", yz: "Yapay zekâ önerisi",
};

/** #/kategorisiz?ay=YYYY-MM | ?ay=tumu (yoksa seçili dönem) */
const hashAy = () => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("ay");

export default function Uncategorized() {
  const ui = useUI();
  const qc = useQueryClient();
  const meta = useMeta().data;
  const [all, setAll] = useState(() => hashAy() === "tumu");
  const ay = all ? "all" : ui.month;
  const q = useQuery({
    queryKey: ["uncategorized", ay],
    queryFn: () => send<Yanit>(`/api/uncategorized/groups?month=${encodeURIComponent(ay)}`),
    refetchOnMount: "always",
  });
  const [remember, setRemember] = useState(true);
  const [secim, setSecim] = useState<Record<string, string>>({});
  const [yz, setYz] = useState<Record<string, Oneri>>({});
  const [sorulan, setSorulan] = useState<Set<string>>(new Set());

  const groups = q.data?.groups ?? [];
  const oneri = (g: Grup) => g.suggestion ?? yz[g.key] ?? null;
  const secili = (g: Grup) => secim[g.key] ?? oneri(g)?.category ?? "";
  const sorulacak = groups.filter((g) => g.kind === "isyeri" && !oneri(g) && !secim[g.key] && !sorulan.has(g.key));
  const ai = q.data?.ai;
  const gelirKat = meta?.income_categories ?? [];
  const giderKat = (meta?.categories ?? []).filter((c) => c !== "Kategorisiz" && c !== "Borç / Alacak" && !gelirKat.includes(c));

  const tazele = () => {
    for (const k of ["uncategorized", "summary", "txns", "transactions", "compare", "year-summary", "budgets", "rules", "meta"])
      void qc.invalidateQueries({ queryKey: [k] });
  };

  const uygula = useMutation({
    mutationFn: ({ g, cat }: { g: Grup; cat: string }) =>
      send<Uygula>("/api/uncategorized/apply", { method: "POST", json: { key: g.key, category: cat, remember, month: ay } }),
    onSuccess: (r, { g, cat }) => {
      qc.setQueryData<Yanit>(["uncategorized", ay], (d) => d && { ...d, groups: d.groups.filter((x) => x.key !== g.key), count: d.count - g.count });
      tazele();
      const ek = r.also_changed > 0 ? ` (+${r.also_changed} başka dönemde)` : "";
      const ogren = r.remembered === "kural" ? " Bundan sonra da böyle." : r.remembered === "kisi" ? ` ${g.label} için hatırlanacak.` : "";
      ui.toast(`${r.changed} işlem ${cat} oldu${ek}.${ogren}`, r.undo_id ? {
        undo: async () => { await send("/api/uncategorized/undo", { method: "POST", json: { undo_id: r.undo_id } }); tazele(); },
      } : undefined);
    },
    onError: (e) => ui.error(e instanceof Error ? e.message : "Kategori verilemedi"),
  });

  const sor = useMutation({
    mutationFn: (keys: string[]) => send<{ suggestions: Record<string, Oneri>; ai: Yanit["ai"] }>("/api/uncategorized/ai-suggest",
      { method: "POST", json: { keys, month: ay } }),
    onSuccess: (r, keys) => {
      setYz((o) => ({ ...o, ...r.suggestions }));
      setSorulan((s) => new Set([...s, ...keys]));
      qc.setQueryData<Yanit>(["uncategorized", ay], (d) => d && { ...d, ai: r.ai });
      const n = Object.keys(r.suggestions).length;
      ui.toast(n ? `${n} işyeri için öneri geldi` : "Yapay zekâ bu işyerleri için emin olamadı; kategoriyi sen seç");
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 402) qc.setQueryData<Yanit>(["uncategorized", ay], (d) => d && { ...d, ai: { ...d.ai, left: 0 } });
      ui.error(e instanceof Error ? e.message : "Yapay zekâya ulaşılamadı");
    },
  });

  const busyKey = uygula.isPending ? uygula.variables?.g.key : null;
  const tarih = (g: Grup) => (g.first_date === g.last_date ? dayMonthTR(g.first_date) : `${dayMonthTR(g.first_date)} – ${dayMonthTR(g.last_date)}`);

  return (
    <div className="space-y-4">
      <button className="btn-ghost -ml-2 px-2 py-1 text-sm" onClick={() => ui.go("islemler")}><ArrowLeft size={16} aria-hidden /> İşlemler</button>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Kategorisizler</h1>
        <div className="flex flex-wrap items-center gap-2">
          {!all && <MonthPicker value={ui.month} onChange={ui.setMonth} />}
          <div role="group" aria-label="Dönem" className="flex gap-1">
            <button aria-pressed={!all} onClick={() => setAll(false)}
              className={cx("rounded-full border px-3 py-1 text-sm", !all ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              {monthLabel(ui.month)} dönemi</button>
            <button aria-pressed={all} onClick={() => setAll(true)}
              className={cx("rounded-full border px-3 py-1 text-sm", all ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              Tüm zaman</button>
          </div>
        </div>
      </header>

      {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
      {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}

      {q.data && groups.length === 0 && (
        <section className="card flex flex-col items-center gap-2 p-6 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400"><Check size={22} aria-hidden /></span>
          <h2 className="font-semibold">Hepsi kategorili</h2>
          <p className="text-sm text-stone-600 dark:text-stone-400">{all ? "Hiçbir dönemde kategorisiz işlem yok." : `${monthLabel(ui.month)} döneminde kategorisiz işlem yok.`}</p>
          {!all && <button className="btn border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" onClick={() => setAll(true)}>Tüm zamana bak</button>}
        </section>
      )}

      {groups.length > 0 && (
        <>
          <p className="text-sm text-stone-600 dark:text-stone-400">{q.data!.count} işlem · {groups.length} grup. Aynı yerdeki işlemler tek seferde kategorilenir.</p>
          <div className="card flex items-center gap-3 p-3">
            <span className="min-w-0 flex-1">
              <span id="hep-boyle" className="block text-sm font-medium">Bundan sonra hep böyle</span>
              <span className="block text-xs text-stone-500 dark:text-stone-400">Aynı işyeri ve kişiye gelecek işlemler de bu kategoriyle gelir.</span>
            </span>
            <input type="checkbox" aria-labelledby="hep-boyle" className="h-5 w-5 accent-blue-600" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          </div>

          {ai?.available && sorulacak.length > 0 && (
            <section className="card space-y-2 p-3">
              <p className="text-sm">{sorulacak.length} işyeri için öneri yok. Yapay zekâya yalnız işyeri adları gönderilir; tutar ve kişi adı gitmez.</p>
              {ai.left === 0 ? (
                <p className="text-xs text-stone-500 dark:text-stone-400">Bu ayki okuma hakkın doldu; ay başında yenilenir.</p>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <button className="btn border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" disabled={sor.isPending}
                    onClick={() => sor.mutate(sorulacak.map((g) => g.key))}><Sparkles size={16} aria-hidden /> {sor.isPending ? "Soruluyor…" : "Yapay zekâya sor"}</button>
                  {ai.left != null && <span className="text-xs text-stone-500 dark:text-stone-400">1 okuma hakkı kullanır · bu ay {ai.left} hakkın var</span>}
                </div>
              )}
            </section>
          )}

          <ul className="grid gap-3 md:grid-cols-2">
            {groups.map((g) => {
              const o = oneri(g);
              const cat = secili(g);
              const Ic = g.kind === "kisi" ? Users : Store;
              return (
                <li key={g.key} data-testid="kat-grup" className={cx("card flex flex-col gap-3 p-3", busyKey === g.key && "opacity-60")}>
                  <div className="flex items-start gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300"><Ic size={18} aria-hidden /></span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium [overflow-wrap:anywhere]">{g.label}</p>
                      <p className="text-xs text-stone-500 dark:text-stone-400">
                        {[`${g.count} işlem`, g.kind === "kisi" ? (g.flow === "gelir" ? "gelen para" : "giden para") : null, tarih(g)].filter(Boolean).join(" · ")}</p>
                    </div>
                    <span className={cx("shrink-0 font-medium tabular-nums", g.flow === "gelir" && "text-green-700 dark:text-green-400")}>
                      {g.flow === "gelir" ? "+" : ""}{tl(g.total)}</span>
                  </div>
                  <div className="space-y-1">
                    <CategoryPicker value={cat} options={g.flow === "gelir" ? gelirKat : giderKat} placeholder="Kategori seç"
                      onChange={(c) => setSecim((s) => ({ ...s, [g.key]: c }))} />
                    {cat && !secim[g.key] && o && <p className="text-xs text-stone-500 dark:text-stone-400">{KAYNAK[o.source]}</p>}
                    {!cat && sorulan.has(g.key) && <p className="text-xs text-stone-500 dark:text-stone-400">Yapay zekâ emin olamadı</p>}
                  </div>
                  <div className="mt-auto flex flex-wrap items-center gap-2">
                    <button className="btn-primary px-4 py-1.5 text-sm" disabled={!cat || uygula.isPending} onClick={() => uygula.mutate({ g, cat })}>
                      {busyKey === g.key ? "Uygulanıyor…" : "Uygula"}</button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
