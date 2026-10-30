// Fiş kalemleri: toplam denetimi, tutmayan satır, önerilen kaydırma (önce/sonra), elle kaydırma, birleştirme,
// satır düzenle/sil/ekle, fiş fotoğrafı (tıklayınca büyür), "Fişten yeniden oku" (karşılaştırıp seç).
// Fiş önizlemesinde (EntryForm taslağı) ve kayıtlı giderde (SavedReceiptItems) aynı parça. Mobil: fis/Kalemler.tsx.
import { AlertTriangle, ArrowDown, ArrowUp, Check, ChevronRight, Combine, Maximize2, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api";
import { cx, parseAmount, tl } from "../../format";
import Modal from "../Modal";
import { checkItems, mergeWithNext, shiftAmounts, TOL, withAmounts, type ItemCheck, type ReceiptCheck, type ReceiptItem, type Reread } from "./check";

const label = (k: ReceiptItem) => `${(k.qty ?? 1) > 1 ? `${k.qty}× ` : ""}${k.name}`;
const num = (v: number | null | undefined) => (v == null ? "" : String(v).replace(".", ","));

/** Yeniden okuma hatası: 429 (günde 3), 404 (fotoğraf yok), 422 (yapay zekâ yok), 502 (okunamadı) anlamlı cümle. */
function rereadError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return "Bugün bu fişi 3 kez okuttun; yarın tekrar dene ya da kalemleri elle düzelt.";
    if (e.status === 404) return "Bu kaydın fiş fotoğrafı saklanmamış; yeniden okunamaz.";
    if (e.status === 422) return e.message && !/^\d+/.test(e.message) ? e.message : "Yeniden okuma bu sunucuda kapalı.";
    if (e.status === 502) return "Fiş yeniden okunamadı. Biraz sonra tekrar dene.";
    if (e.message && !/^\d+/.test(e.message)) return e.message;
  }
  return (e as Error)?.message || "Fiş yeniden okunamadı";
}

/** Toplam tutmuyor mu: üstteki uyarı cümlesi (Kaydet'in yanında da kullanılır). */
export function checkSentence(c: ReceiptCheck): string {
  if (c.diff != null && Math.abs(c.diff) > TOL)
    return `Kalemler ${tl(c.items_total)}, fiş ${tl(c.receipt_total!)}: ${tl(Math.abs(c.diff))} ${c.diff > 0 ? "eksik" : "fazla"}`;
  return "Bazı satırlarda adet × birim fiyat tutmuyor";
}

export default function ReceiptItems({ items, total, onChange, photo, onReread, note, collapsed = 6 }: {
  items: ReceiptItem[];
  /** fiş toplamı (önizlemede formdaki tutar, kayıtlıda kaydın tutarı) */
  total: number | null | undefined;
  onChange: (items: ReceiptItem[]) => void;
  /** fiş fotoğrafının adresi */
  photo?: string | null;
  /** "Fişten yeniden oku": sonucu döner, hiçbir şey kaydedilmez */
  onReread?: () => Promise<Reread>;
  note?: string | null;
  collapsed?: number;
}) {
  const chk = useMemo(() => checkItems(items, total), [items, total]);
  const [all, setAll] = useState(false);
  const [edit, setEdit] = useState<number | "new" | null>(null);
  const [preview, setPreview] = useState<{ amounts: (number | null)[]; title: string } | null>(null);
  const [zoom, setZoom] = useState(false);
  const [reread, setReread] = useState<{ busy: boolean; r?: Reread; err?: string } | null>(null);
  const bad = !chk.ok && items.length > 0;
  const shown = all || bad ? items : items.slice(0, collapsed);

  async function doReread() {
    if (!onReread) return;
    setReread({ busy: true });
    try { setReread({ busy: false, r: await onReread() }); } catch (e) { setReread({ busy: false, err: rereadError(e) }); }
  }

  return (
    <section className="overflow-hidden rounded-lg border border-stone-200 dark:border-stone-700" aria-labelledby="ri-h">
      <div className="flex items-center gap-3 px-3 pt-3 pb-2">
        {photo && (
          <button type="button" className="group relative h-16 w-12 shrink-0 overflow-hidden rounded-md border border-stone-200 bg-stone-100 dark:border-stone-700 dark:bg-stone-800"
            onClick={() => setZoom(true)} aria-label="Fiş fotoğrafını büyüt">
            <img src={photo} alt="" className="size-full object-cover" />
            <span className="absolute inset-0 flex items-center justify-center bg-black/0 text-white opacity-0 transition group-hover:bg-black/30 group-hover:opacity-100" aria-hidden>
              <Maximize2 size={16} /></span>
          </button>
        )}
        <div className="min-w-0 flex-1">
          <h3 id="ri-h" className="text-sm font-semibold">Ürünler</h3>
          <p className="text-xs text-stone-500 tabular-nums dark:text-stone-400">{items.length} kalem · {tl(chk.items_total)}</p>
        </div>
        {chk.ok && items.length > 0 && total != null && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-green-50 px-1.5 py-0.5 text-[11px] font-semibold text-green-700 dark:bg-green-950/50 dark:text-green-400">
            <Check size={12} aria-hidden /> Fişle tuttu</span>
        )}
      </div>
      {note && <p className="px-3 pb-2 text-xs text-stone-500 dark:text-stone-400">{note}</p>}

      {bad && (
        <div className="mx-3 mb-2 flex gap-2 rounded-lg bg-amber-50 p-2.5 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" role="alert">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="font-medium">{checkSentence(chk)}</p>
            {chk.suggested_shift && (<>
              <p className="text-xs">Fiyatlar {chk.suggested_shift.from_index + 1}. satırdan itibaren bir satır {chk.suggested_shift.direction === "down" ? "yukarıda" : "aşağıda"} okunmuş olabilir.</p>
              <button type="button" className="btn-primary mt-1 px-2.5 py-1 text-xs"
                onClick={() => setPreview({ amounts: chk.suggested_shift!.amounts, title: "Önerilen düzeltme" })}>Fiyatları kaydır: önce / sonra</button>
            </>)}
            {!chk.suggested_shift && (
              <p className="text-xs">{chk.items.some((c) => c.mismatch)
                ? "İşaretli satıra tıkla: tutarını düzelt, fiyatları kaydır ya da altındakiyle birleştir."
                : "Yanlış okunan kalemi düzelt, eksik kalemi ekle ya da fişten yeniden oku."}</p>
            )}
          </div>
        </div>
      )}

      <ul className="divide-y divide-stone-100 border-t border-stone-100 dark:divide-stone-800 dark:border-stone-800">
        {shown.map((k, i) => {
          const c = chk.items[i];
          return (
            <li key={i}>
              <button type="button" onClick={() => setEdit(i)}
                className={cx("flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-stone-50 dark:hover:bg-stone-800/50",
                  c?.mismatch && "bg-amber-50 shadow-[inset_3px_0_0_var(--color-amber-500)] hover:bg-amber-100/70 dark:bg-amber-950/30 dark:hover:bg-amber-950/50")}
                aria-label={`${label(k)}, ${k.amount != null ? tl(k.amount) : "tutar yok"}${c?.mismatch ? ", tutmuyor" : ""}: düzenle`}>
                <span className="w-5 shrink-0 pt-px text-right text-xs text-stone-400 tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="block break-words">{label(k)}</span>
                  {c?.mismatch && (
                    <span className="mt-0.5 flex items-center gap-1 text-xs font-semibold text-amber-800 dark:text-amber-300">
                      <AlertTriangle size={12} aria-hidden /> {k.qty ?? 1} × {tl(k.unit_price ?? 0)} = {tl(c.expected ?? 0)} olmalı</span>
                  )}
                </span>
                <span className={cx("shrink-0 tabular-nums", c?.mismatch ? "font-semibold text-amber-800 dark:text-amber-300" : "text-stone-700 dark:text-stone-300")}>
                  {k.amount != null ? tl(k.amount) : "—"}</span>
                <ChevronRight size={14} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
      {!bad && items.length > collapsed && (
        <button type="button" className="w-full border-t border-stone-100 py-2 text-sm text-blue-600 hover:bg-stone-50 dark:border-stone-800 dark:text-blue-400 dark:hover:bg-stone-800/50"
          onClick={() => setAll(!all)}>{all ? "Daha az göster" : `${items.length - collapsed} kalem daha göster`}</button>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-stone-100 px-3 py-2 dark:border-stone-800">
        <button type="button" className="btn-ghost px-2 text-xs" onClick={() => setEdit("new")}><Plus size={14} aria-hidden /> Kalem ekle</button>
        {onReread && (
          <button type="button" className="btn-ghost px-2 text-xs" disabled={reread?.busy} onClick={() => void doReread()}>
            <RefreshCw size={14} className={cx(reread?.busy && "animate-spin")} aria-hidden /> {reread?.busy ? "Yeniden okunuyor…" : "Fişten yeniden oku"}</button>
        )}
      </div>

      {edit != null && (
        <ItemForm k={edit === "new" ? { name: "", qty: 1, amount: null } : items[edit]} index={edit === "new" ? null : edit}
          count={items.length} check={edit === "new" ? undefined : chk.items[edit]} onClose={() => setEdit(null)}
          onSave={(k) => { onChange(edit === "new" ? [...items, k] : items.map((x, j) => (j === edit ? k : x))); setEdit(null); }}
          onDelete={() => { if (edit !== "new") onChange(items.filter((_, j) => j !== edit)); setEdit(null); }}
          onShift={(d) => { if (edit !== "new") { setPreview({ amounts: shiftAmounts(items, edit, d), title: `${edit + 1}. satırdan itibaren fiyatlar bir ${d === "down" ? "aşağı" : "yukarı"}` }); setEdit(null); } }}
          onMerge={() => { if (edit !== "new") onChange(mergeWithNext(items, edit)); setEdit(null); }} />
      )}
      {preview && (
        <BeforeAfter title={preview.title} before={items} after={withAmounts(items, preview.amounts)} total={total}
          onClose={() => setPreview(null)} onApply={() => { onChange(withAmounts(items, preview.amounts)); setPreview(null); }} />
      )}
      {reread && !reread.busy && (reread.err || !reread.r ? (
        <Modal title="Yeniden okunamadı" onClose={() => setReread(null)} footer={<><div className="flex-1" /><button className="btn-primary" onClick={() => setReread(null)}>Tamam</button></>}>
          <p className="flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" role="alert">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />{reread.err}</p>
        </Modal>
      ) : (
        <BeforeAfter title="Yeniden okunan kalemler" before={items} after={reread.r.items.filter((i) => i.name)} total={total ?? reread.r.total}
          applyLabel="Yeniden okunanı kullan" onClose={() => setReread(null)}
          onApply={() => { onChange(reread.r!.items.filter((i) => i.name)); setReread(null); }} />
      ))}
      {zoom && photo && <PhotoViewer url={photo} onClose={() => setZoom(false)} />}
    </section>
  );
}

/** Bir kalem: ad, adet, tutar; bu satırdan itibaren fiyatları kaydır, altındakiyle birleştir, sil. */
function ItemForm({ k, index, count, check, onClose, onSave, onDelete, onShift, onMerge }: {
  k: ReceiptItem; index: number | null; count: number; check?: ItemCheck;
  onClose: () => void; onSave: (k: ReceiptItem) => void; onDelete: () => void; onShift: (d: "up" | "down") => void; onMerge: () => void;
}) {
  const [name, setName] = useState(k.name);
  const [qty, setQty] = useState(num(k.qty ?? 1));
  const [amount, setAmount] = useState(num(k.amount));
  const q = parseAmount(qty), a = amount.trim() === "" ? null : parseAmount(amount);
  const ok = name.trim().length > 0 && (a == null || Number.isFinite(a)) && Number.isFinite(q) && q > 0;
  const save = () => {
    const changed = a !== k.amount || q !== (k.qty ?? 1);
    // tutar ya da adet elle değiştiyse okunan birim fiyat artık denetlenmesin
    onSave({ ...k, name: name.trim(), qty: q, amount: a,
      unit_price: changed ? (a != null && q ? Math.round((a / q) * 100) / 100 : null) : k.unit_price ?? null });
  };
  const row = "flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-stone-50 dark:hover:bg-stone-800";
  return (
    <Modal title={index == null ? "Kalem ekle" : `${index + 1}. kalem`} onClose={onClose} footer={<>
      {index != null && <button className="btn-danger" onClick={onDelete}><Trash2 size={15} aria-hidden /> Kalemi sil</button>}
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={!ok} onClick={save}>{index == null ? "Ekle" : "Kaydet"}</button>
    </>}>
      <div className="space-y-3">
        {check?.mismatch && (
          <p className="flex gap-2 rounded-lg bg-amber-50 p-2.5 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            Fişte {k.qty ?? 1} adet × {tl(k.unit_price ?? 0)} yazıyor; tutar {tl(check.expected ?? 0)} olmalı, {k.amount != null ? tl(k.amount) : "boş"} okundu.
          </p>
        )}
        <div>
          <label className="label" htmlFor="ri-name">Ürün adı</label>
          <input id="ri-name" className="input" value={name} maxLength={80} autoFocus={index == null} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="ri-qty">Adet</label>
            <input id="ri-qty" className="input text-right" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="ri-amt">Tutar (₺)</label>
            <input id="ri-amt" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
        </div>
        {index != null && (
          <div className="space-y-1 border-t border-stone-100 pt-2 dark:border-stone-800">
            <button type="button" className={row} onClick={() => onShift("down")}>
              <ArrowDown size={16} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
              <span><span className="block font-medium">Bu satırdan itibaren fiyatları aşağı kaydır</span>
                <span className="block text-xs text-stone-500 dark:text-stone-400">Her fiyat bir alt satıra iner; önce / sonra gösterilir</span></span>
            </button>
            <button type="button" className={row} onClick={() => onShift("up")}>
              <ArrowUp size={16} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
              <span><span className="block font-medium">Bu satırdan itibaren fiyatları yukarı kaydır</span>
                <span className="block text-xs text-stone-500 dark:text-stone-400">Her fiyat bir üst satıra çıkar; önce / sonra gösterilir</span></span>
            </button>
            {index < count - 1 && (
              <button type="button" className={row} onClick={onMerge}>
                <Combine size={16} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
                <span><span className="block font-medium">Altındakiyle birleştir</span>
                  <span className="block text-xs text-stone-500 dark:text-stone-400">İki satıra bölünmüş tek ürün: ad birleşir, tutar korunur</span></span>
              </button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

/** Önce / sonra: değişen satırlar vurgulu (yalnız renk değil: "değişti" işareti), altta iki toplam ve fişle farkı. */
function BeforeAfter({ title, before, after, total, onClose, onApply, applyLabel = "Uygula" }: {
  title: string; before: ReceiptItem[]; after: ReceiptItem[]; total: number | null | undefined;
  onClose: () => void; onApply: () => void; applyLabel?: string;
}) {
  const b = checkItems(before, total), a = checkItems(after, total);
  const gap = (c: ReceiptCheck) => (c.diff == null ? "" : Math.abs(c.diff) <= TOL ? "fişle tuttu ✓" : `fark ${tl(Math.abs(c.diff))}`);
  const n = Math.max(before.length, after.length);
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" onClick={onApply}><Check size={15} aria-hidden /> {applyLabel}</button>
    </>}>
      <p className="mb-2 text-xs text-stone-500 dark:text-stone-400">Hiçbir şey kendiliğinden kaydedilmez; uygularsan kalemler değişir, sonra kaydedersin.</p>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-stone-200 text-xs text-stone-500 uppercase dark:border-stone-700 dark:text-stone-400">
            <th className="py-1.5 pr-2 text-left font-semibold">Ürün</th>
            <th className="py-1.5 pl-2 text-right font-semibold">Önce</th>
            <th className="py-1.5 pl-2 text-right font-semibold">Sonra</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
          {Array.from({ length: n }, (_, i) => {
            const x = before[i], y = after[i];
            const changed = !x || !y || x.amount !== y.amount || x.name !== y.name;
            return (
              <tr key={i} className={cx(changed && "bg-blue-50 dark:bg-blue-950/30")}>
                <td className="py-1.5 pr-2 break-words">
                  {label(y ?? x)}{changed && <span className="ml-1 text-[11px] font-semibold text-blue-700 dark:text-blue-300">· değişti</span>}</td>
                <td className="py-1.5 pl-2 text-right whitespace-nowrap text-stone-500 tabular-nums dark:text-stone-400">{x?.amount != null ? tl(x.amount) : "—"}</td>
                <td className={cx("py-1.5 pl-2 text-right whitespace-nowrap tabular-nums", changed && "font-semibold")}>{y?.amount != null ? tl(y.amount) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-stone-200 font-semibold dark:border-stone-700">
            <td className="py-2 pr-2">Toplam{total != null && <span className="block text-xs font-normal text-stone-500 dark:text-stone-400">fiş {tl(total)}</span>}</td>
            <td className="py-2 pl-2 text-right align-top whitespace-nowrap tabular-nums">{tl(b.items_total)}
              <span className="block text-[11px] font-normal text-stone-500 dark:text-stone-400">{gap(b)}</span></td>
            <td className="py-2 pl-2 text-right align-top whitespace-nowrap tabular-nums">{tl(a.items_total)}
              <span className={cx("block text-[11px] font-normal", a.ok ? "text-green-700 dark:text-green-400" : "text-stone-500 dark:text-stone-400")}>{gap(a)}</span></td>
          </tr>
        </tfoot>
      </table>
    </Modal>
  );
}

/** Tam ekran fiş fotoğrafı: sığdırılır; tıklayınca gerçek boyuta büyür (kaydırılır), yine tıklayınca sığar. Esc ve ✕ kapatır.
 *  Fotoğraf için koyu zemin iki temada da aynı. */
export function PhotoViewer({ url, onClose }: { url: string; onClose: () => void }) {
  const [big, setBig] = useState(false);
  const closeBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[70] flex flex-col bg-black/95" role="dialog" aria-modal="true" aria-label="Fiş fotoğrafı">
      <div className="flex items-center justify-between gap-2 px-3 py-2 pt-[max(0.5rem,var(--sat))] text-sm text-white/80">
        <span>{big ? "Gerçek boyut · tıkla: sığdır" : "Tıkla: büyüt"}</span>
        <button ref={closeBtn} className="rounded-full p-2 text-white hover:bg-white/10" onClick={onClose} aria-label="Kapat"><X size={20} /></button>
      </div>
      <div className={cx("min-h-0 flex-1", big ? "overflow-auto" : "flex items-center justify-center overflow-hidden p-2")}>
        <button type="button" className={cx("block", !big && "max-h-full max-w-full", big ? "cursor-zoom-out" : "cursor-zoom-in")}
          aria-label={big ? "Sığdır" : "Gerçek boyutta göster"} onClick={() => setBig(!big)}>
          <img src={url} alt="Fiş" className={cx(big ? "max-w-none" : "max-h-[calc(100dvh-4rem)] max-w-full object-contain")} />
        </button>
      </div>
    </div>
  );
}
