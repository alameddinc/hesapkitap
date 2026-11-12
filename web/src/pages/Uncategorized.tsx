import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, CheckCheck, ListChecks, Pencil, Sparkles, Store, Users } from "lucide-react";
import { useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import Modal from "../components/Modal";
import MonthPicker from "../components/MonthPicker";
import Segmented from "../components/Segmented";
import { useMeta } from "../components/hooks";
import { useDocQuota } from "../components/DocQuota";
import { ApiError, send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { cx, dayMonthTR, tl, periodLabel } from "../format";
import { useUI } from "../ui";
import SorguDurumu, { SatirIskelet } from "../components/SorguDurumu";

// Kategorisizleri düzenle (sunucu app/uncategorized.py; mobil src/screens/Kategorisiz.tsx ile aynı akış).
// Küme Özet'teki "N işlem kategorisiz" uyarısıyla aynı; işyeri/kişi grupları, ücretsiz öneri. Öneriye tıklamak ya da
// seçiciden kategori seçmek HEMEN uygular (ayrı "Uygula" adımı yok: seçip bırakan kullanıcı uygulandı sanıyordu), Geri al,
// "Bundan sonra hep böyle" (kural / kişi kategorisi), önerisi olmayan işyerleri için yapay zekâ (1 okuma hakkı).
// Toplu: önerilenlerin hepsini uygula, çoklu seçim → tek kategori, tek seferlikleri Diğer yap (hepsi tek Geri al;
// POST /api/uncategorized/apply-many). "Adı düzelt": görünen işyeri adı (POST /api/merchants/rename).

type Kaynak = "gecmis" | "kural" | "bilinen" | "yz";
interface Oneri { category: string; source: Kaynak; confidence: number }
interface Grup {
  key: string; label: string; kind: "isyeri" | "kisi"; flow: "gelir" | "gider"; count: number; total: number;
  sample_ids: string[]; first_date: string; last_date: string; suggestion: Oneri | null;
  one_off?: boolean; renamable?: boolean;
}
interface Yanit {
  month: string | null; groups: Grup[]; count: number; ai: { available: boolean; left: number | null };
  one_off_max?: number; ai_max?: number;
}
interface Uygula { changed: number; also_changed: number; remembered: "kural" | "kisi" | null; undo_id: string | null }
interface Toplu { groups: number; changed: number; also_changed: number; rules: number; people: number; skipped: string[]; undo_id: string | null }
interface AdYanit { display: string | null; sources: string[]; changed: number; undo_id: string | null }
type Sira = "total" | "count";

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
  const [yz, setYz] = useState<Record<string, Oneri>>({});
  const [sorulan, setSorulan] = useState<Set<string>>(new Set());

  const [sira, setSira] = useState<Sira>("total");
  const [secMod, setSecMod] = useState(false);
  const [sec, setSec] = useState<Set<string>>(new Set());
  const [topluKat, setTopluKat] = useState<string | null>(null); // çoklu seçim için kategori penceresi (açıkken string)
  const [adGrup, setAdGrup] = useState<Grup | null>(null);

  const groups = [...(q.data?.groups ?? [])].sort((a, b) => sira === "total"
    ? Math.abs(b.total) - Math.abs(a.total) || b.count - a.count || a.label.localeCompare(b.label, "tr")
    : b.count - a.count || Math.abs(b.total) - Math.abs(a.total) || a.label.localeCompare(b.label, "tr"));
  const oneri = (g: Grup) => g.suggestion ?? yz[g.key] ?? null;
  const sorulacak = groups.filter((g) => g.kind === "isyeri" && !oneri(g) && !sorulan.has(g.key));
  const aiMax = q.data?.ai_max ?? 40;
  const hazir = groups.filter((g) => oneri(g));
  const tekler = groups.filter((g) => g.one_off);
  const secilen = groups.filter((g) => sec.has(g.key));
  const secAkis = secilen[0]?.flow ?? null;
  const ai = q.data?.ai;
  const quotaReads = useDocQuota().reads; // tek sayaç: hak dolu metni ve dönem sunucudan
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
      const ogren = r.remembered === "kural" ? " · bundan sonra da" : r.remembered === "kisi" ? ` · ${g.label} için hatırlanacak` : "";
      ui.toast(`${r.changed} işlem → ${cat}${ek}${ogren}`, r.undo_id ? {
        undo: async () => { await send("/api/uncategorized/undo", { method: "POST", json: { undo_id: r.undo_id } }); tazele(); },
      } : undefined);
    },
    onError: (e) => ui.error(e instanceof Error ? e.message : "Kategori verilemedi"),
  });

  const cikar = (keys: string[]) => qc.setQueryData<Yanit>(["uncategorized", ay], (d) => {
    if (!d) return d;
    const gone = new Set(keys);
    return { ...d, groups: d.groups.filter((x) => !gone.has(x.key)), count: d.count - d.groups.filter((x) => gone.has(x.key)).reduce((n, x) => n + x.count, 0) }; // parity-ok: SUNUM (işlem sayısı, para değil)
  });
  const geriAl = (id: string | null) => (id ? {
    undo: async () => { await send("/api/uncategorized/undo", { method: "POST", json: { undo_id: id } }); tazele(); },
  } : undefined);

  const toplu = useMutation({
    mutationFn: ({ items, hatirla }: { items: { key: string; category: string }[]; hatirla: boolean }) =>
      send<Toplu>("/api/uncategorized/apply-many", { method: "POST", json: { items, remember: hatirla, month: ay } }),
    onSuccess: (r, { items }) => {
      cikar(items.map((i) => i.key));
      setSec(new Set()); setSecMod(false); setTopluKat(null);
      tazele();
      const ek = r.also_changed > 0 ? ` (+${r.also_changed} başka dönemde)` : "";
      ui.toast(`${r.changed} işlem, ${r.groups} grup kategorilendi${ek}.`, geriAl(r.undo_id));
    },
    onError: (e) => ui.error(e instanceof Error ? e.message : "Kategori verilemedi"),
  });
  const hepsiniUygula = () => toplu.mutate({ items: hazir.map((g) => ({ key: g.key, category: oneri(g)!.category })), hatirla: remember });
  const tekleriDiger = async () => {
    const esik = tl(q.data?.one_off_max ?? 250);
    const ok = await ask(`${tekler.length} tek seferlik işlem Diğer olsun mu?\n\nTek işlemli ve ${esik} ya da daha az tutan giderler "Diğer" kategorisine alınır. Kural öğrenilmez; Geri al ile hepsi geri gelir.`,
      { confirm: "Diğer yap" });
    if (ok) toplu.mutate({ items: tekler.map((g) => ({ key: g.key, category: "Diğer" })), hatirla: false });
  };
  const secDegis = (g: Grup) => setSec((s) => {
    const n = new Set(s);
    if (n.has(g.key)) n.delete(g.key); else n.add(g.key);
    return n;
  });

  const adDuzelt = useMutation({
    mutationFn: ({ g, display }: { g: Grup; display: string }) =>
      send<AdYanit>("/api/merchants/rename", { method: "POST", json: { key: g.key, display, month: ay } }),
    onSuccess: (r) => {
      setAdGrup(null);
      tazele();
      ui.toast(r.display ? `Ad düzeltildi: ${r.display} (${r.changed} işlem)` : "Ekstredeki ada dönüldü", geriAl(r.undo_id));
    },
    onError: (e) => ui.error(e instanceof Error ? e.message : "Ad düzeltilemedi"),
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
  const topluBusy = toplu.isPending ? new Set(toplu.variables?.items.map((i) => i.key)) : null;
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
              {periodLabel(ui.month)} dönemi</button>
            <button aria-pressed={all} onClick={() => setAll(true)}
              className={cx("rounded-full border px-3 py-1 text-sm", all ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
              Tüm zaman</button>
          </div>
        </div>
      </header>

      <SorguDurumu q={q} etiket="Kategorisiz işlemler yükleniyor"><SatirIskelet n={4} /></SorguDurumu>

      {q.data && groups.length === 0 && (
        <section className="card flex flex-col items-center gap-2 p-6 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400"><Check size={22} aria-hidden /></span>
          <h2 className="font-semibold">Hepsi kategorili</h2>
          <p className="text-sm text-stone-600 dark:text-stone-400">{all ? "Hiçbir dönemde kategorisiz işlem yok." : `${periodLabel(ui.month)} döneminde kategorisiz işlem yok.`}</p>
          {!all && <button className="btn border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" onClick={() => setAll(true)}>Tüm zamana bak</button>}
        </section>
      )}

      {groups.length > 0 && (
        <>
          <p className="text-sm text-stone-600 dark:text-stone-400">{q.data!.count} işlem · {groups.length} grup. Kategori seçince aynı yerdeki işlemlerin hepsine hemen uygulanır.</p>
          <div className="card flex items-center gap-3 p-3">
            <span className="min-w-0 flex-1">
              <span id="hep-boyle" className="block text-sm font-medium">Bundan sonra hep böyle</span>
              <span className="block text-xs text-stone-500 dark:text-stone-400">Aynı işyeri ve kişiye gelecek işlemler de bu kategoriyle gelir.</span>
            </span>
            <input type="checkbox" aria-labelledby="hep-boyle" className="h-5 w-5 accent-blue-600" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          </div>

          <section className="card space-y-3 p-3" aria-label="Toplu işlemler">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium">Toplu işlemler</span>
              <div className="w-full sm:w-auto">
                <Segmented<Sira> label="Sıralama" value={sira} onChange={setSira}
                  options={[{ value: "total", label: "En çok tutan" }, { value: "count", label: "En çok işlem" }]} />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary px-3 py-1.5 text-sm" disabled={!hazir.length || toplu.isPending || secMod} onClick={hepsiniUygula}>
                <CheckCheck size={16} aria-hidden /> Önerilenlerin hepsini uygula ({hazir.length})</button>
              {tekler.length > 0 && (
                <button className="btn border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" disabled={toplu.isPending || secMod} onClick={() => void tekleriDiger()}>
                  Tek seferlikleri Diğer yap ({tekler.length})</button>
              )}
              <button className={cx("btn border px-3 py-1.5 text-sm", secMod ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300" : "border-stone-300 dark:border-stone-700")}
                aria-pressed={secMod} onClick={() => { setSecMod((v) => !v); setSec(new Set()); }}>
                <ListChecks size={16} aria-hidden /> {secMod ? "Seçimi bitir" : "Seç"}</button>
            </div>
            {secMod && <p className="text-xs text-stone-500 dark:text-stone-400">Grupları işaretle, hepsine tek kategori ver. Gelir ve gider grupları birlikte seçilemez.</p>}
          </section>

          {ai?.available && sorulacak.length > 0 && (
            <section className="card space-y-2 p-3">
              <p className="text-sm">{sorulacak.length} işyeri için öneri yok. Yapay zekâya yalnız işyeri adları gönderilir; tutar ve kişi adı gitmez.</p>
              {sorulacak.length > aiMax && <p className="text-xs text-stone-500 dark:text-stone-400">Tek seferde en çok {aiMax} işyeri sorulur; kalanı için bir daha sorabilirsin.</p>}
              {ai.left === 0 ? (
                <p className="text-xs text-stone-500 dark:text-stone-400">{quotaReads?.texts ? `${quotaReads.texts.title}. ${quotaReads.texts.when}` : "Bu ayki okuma hakkın doldu; ay başında yenilenir."}</p>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <button className="btn border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" disabled={sor.isPending}
                    onClick={() => sor.mutate(sorulacak.slice(0, aiMax).map((g) => g.key))}><Sparkles size={16} aria-hidden /> {sor.isPending ? "Soruluyor…" : sorulacak.length > 1 ? "Yapay zekâya hepsini sor" : "Yapay zekâya sor"}</button>
                  {ai.left != null && <span className="text-xs text-stone-500 dark:text-stone-400">1 okuma hakkı kullanır · {quotaReads?.limited_by === "day" ? "bugün" : "bu ay"} {ai.left} hakkın var</span>}
                </div>
              )}
            </section>
          )}

          <ul className="grid gap-3 md:grid-cols-2">
            {groups.map((g) => {
              const o = oneri(g);
              const busy = busyKey === g.key;
              const Ic = g.kind === "kisi" ? Users : Store;
              const kapali = !!secAkis && secAkis !== g.flow;
              const isaretli = sec.has(g.key);
              return (
                <li key={g.key} data-testid="kat-grup" className={cx("card flex flex-col gap-3 p-3", (busyKey === g.key || topluBusy?.has(g.key)) && "opacity-60",
                  isaretli && "ring-2 ring-blue-600")}>
                  <div className="flex items-start gap-3">
                    {secMod && (
                      <input type="checkbox" className="mt-2 h-5 w-5 shrink-0 accent-blue-600" checked={isaretli} disabled={kapali}
                        aria-label={`${g.label} seç`} title={kapali ? "Gelir ve gider grupları birlikte seçilemez" : undefined} onChange={() => secDegis(g)} />
                    )}
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300"><Ic size={18} aria-hidden /></span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium [overflow-wrap:anywhere]">{g.label}</p>
                      <p className="text-xs text-stone-500 dark:text-stone-400">
                        {[`${g.count} işlem`, g.kind === "kisi" ? (g.flow === "gelir" ? "gelen para" : "giden para") : null, tarih(g)].filter(Boolean).join(" · ")}</p>
                    </div>
                    <span className={cx("shrink-0 font-medium tabular-nums", g.flow === "gelir" && "text-green-700 dark:text-green-400")}>
                      {g.flow === "gelir" ? "+" : ""}{tl(g.total)}</span>
                  </div>
                  <div className="space-y-2">
                    {o && (
                      <div>
                        <button className="btn-primary w-full justify-center px-3 py-2 text-sm" disabled={uygula.isPending || secMod}
                          onClick={() => uygula.mutate({ g, cat: o.category })}>
                          {busy ? "Uygulanıyor…" : <><Check size={16} aria-hidden /> <span className="[overflow-wrap:anywhere]">{o.category} yap</span></>}</button>
                        <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">Öneri · {KAYNAK[o.source]}</p>
                      </div>
                    )}
                    <div className={cx(uygula.isPending && "pointer-events-none")}>
                      <label className="sr-only" htmlFor={`kat-${g.key}`}>{g.label} için {o ? "başka kategori" : "kategori"}</label>
                      <CategoryPicker id={`kat-${g.key}`} value="" options={g.flow === "gelir" ? gelirKat : giderKat}
                        placeholder={busy && !o ? "Uygulanıyor…" : o ? "Başka kategori seç" : "Kategori seç"}
                        onChange={(c) => { if (c) uygula.mutate({ g, cat: c }); }} />
                    </div>
                    {!o && sorulan.has(g.key) && <p className="text-xs text-stone-500 dark:text-stone-400">Yapay zekâ emin olamadı</p>}
                  </div>
                  <div className="mt-auto flex flex-wrap items-center gap-2 empty:hidden">
                    {g.renamable && (
                      <button className="btn-ghost px-2 py-1.5 text-sm" onClick={() => setAdGrup(g)}><Pencil size={14} aria-hidden /> Adı düzelt</button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          {secMod && (
            <div className="sticky bottom-[calc(5rem+var(--sab))] z-30 md:bottom-4">
              <div className="card flex flex-wrap items-center gap-2 border-blue-600 p-3 shadow-lg">
                <span className="min-w-0 flex-1 text-sm font-medium" aria-live="polite">{secilen.length ? `${secilen.length} grup seçildi` : "Grup seç"}</span>
                <button className="btn-ghost px-3 py-1.5 text-sm" onClick={() => { setSecMod(false); setSec(new Set()); }}>Vazgeç</button>
                <button className="btn-primary px-3 py-1.5 text-sm" disabled={!secilen.length || toplu.isPending} onClick={() => setTopluKat("")}>Kategori ver</button>
              </div>
            </div>
          )}
        </>
      )}

      {topluKat !== null && (
        <Modal title={`${secilen.length} gruba kategori`} onClose={() => setTopluKat(null)} footer={<>
          <div className="flex-1" />
          <button className="btn-ghost" onClick={() => setTopluKat(null)}>Vazgeç</button>
          <button className="btn-primary" disabled={!topluKat || toplu.isPending}
            onClick={() => toplu.mutate({ items: secilen.map((g) => ({ key: g.key, category: topluKat })), hatirla: remember })}>
            {toplu.isPending ? "Uygulanıyor…" : "Uygula"}</button>
        </>}>
          <div className="space-y-2">
            <CategoryPicker value={topluKat} options={secAkis === "gelir" ? gelirKat : giderKat} onChange={setTopluKat} />
            <p className="text-xs text-stone-500 dark:text-stone-400">
              {secilen.reduce((n, g) => n + g.count, 0) /* parity-ok: SUNUM işlem sayısı */} işlem · {remember ? "Bundan sonra da böyle (açık)" : "Yalnız bu kayıtlar"}</p>
          </div>
        </Modal>
      )}

      {adGrup && <AdDuzelt g={adGrup} busy={adDuzelt.isPending} onClose={() => setAdGrup(null)} onSave={(display) => adDuzelt.mutate({ g: adGrup, display })} />}
    </div>
  );
}

/** Görünen işyeri adını düzelt: ekstredeki ham kayıt değişmez; boş → ekstredeki ada dön. */
export function AdDuzelt({ g, busy, onClose, onSave }: { g: { label: string }; busy: boolean; onClose: () => void; onSave: (display: string) => void }) {
  const [ad, setAd] = useState(g.label);
  const temiz = ad.trim();
  return (
    <Modal title="Adı düzelt" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={busy || !temiz || temiz === g.label} onClick={() => onSave(temiz)}>{busy ? "Kaydediliyor…" : "Kaydet"}</button>
    </>}>
      <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (temiz && temiz !== g.label) onSave(temiz); }}>
        <label className="label" htmlFor="ad-duzelt">İşyeri adı</label>
        <input id="ad-duzelt" className="input" value={ad} maxLength={60} autoFocus onChange={(e) => setAd(e.target.value)} />
        <p className="text-xs text-stone-500 dark:text-stone-400">Bu işyerinin bütün kart işlemlerinde bu ad görünür; ekstredeki kayıt değişmez.</p>
        <button type="button" className="btn-ghost -ml-2 px-2 py-1 text-sm" disabled={busy} onClick={() => onSave("")}>Ekstredeki ada dön</button>
      </form>
    </Modal>
  );
}
