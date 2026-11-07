// Alışveriş satırına dokununca: düzenle / düzelt penceresi (yetenek shopping_units). Ad (yazarken öneriler, yetenek
// shopping_autofill: GET /api/shopping-list/suggest — seçilen öneri boyu sabitler), miktar (küçük adım düğmeleri; adet 1,
// kg 0,5, g 100, L 0,5, ml 250; ondalık yazılabilir) ve birim (adet kg g L ml), adette boy (GET /{id}/sizes). Satır
// tanınmadıysa / boyu yoksa / birimi uymuyorsa sunucunun ipucu en üstte. "Listeden çıkar" de burada.
import { useQuery } from "@tanstack/react-query";
import { Check, Info, Minus, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { cx } from "../format";
import { send } from "../lib/apiError";
import type { UnpricedInfo } from "../lib/priceAdvice";
import { chainName, convertQty, fmtQty, isUnit, parseQty, stepQty, UNITS, validQty, type Unit } from "../lib/shopList";
import Modal from "./Modal";
import { SuggestList, useSuggest, type Suggestion } from "./ShopAutofill";

export interface EditableItem {
  id: number; text: string; qty: number | null; unit?: string | null; group_key: string | null;
  group?: { key: string; label: string; pending?: boolean } | null;
}
interface SizeOpt { key: string; label: string; size_label: string; chain: string | null; price: number | null; unit_price_per?: number | null; per_unit?: string | null }
export interface ItemPatch { text?: string; qty?: number; unit?: Unit; group_key?: string | null }

export default function ShopItemDialog({ it, hint, autofill, money, busy, onClose, onSave, onRemove }: {
  it: EditableItem; hint?: UnpricedInfo; autofill: boolean; money: (n: number) => string; busy?: boolean;
  onClose: () => void; onSave: (p: ItemPatch) => void; onRemove: () => void;
}) {
  const unit0: Unit = isUnit(it.unit) ? it.unit : "adet";
  const [text, setText] = useState(it.text);
  const [unit, setUnit] = useState<Unit>(unit0);
  const [qtyS, setQtyS] = useState(fmtQty(it.qty ?? 1));
  const [picked, setPicked] = useState<Suggestion | null>(null);
  const cur = it.group?.key ?? it.group_key;
  const [size, setSize] = useState<string | null>(cur);
  const [sugOpen, setSugOpen] = useState(hint?.reason === "no_group");
  const [active, setActive] = useState(-1);
  const lid = useId().replace(/:/g, "");
  const renamed = text.trim() !== it.text;
  const qty = parseQty(qtyS);
  const okQty = validQty(qty, unit);
  const sug = useSuggest(text, autofill && sugOpen);
  const sugItems = (sug.data ?? []).filter((s) => !(s.text === it.text && (s.group_key ?? null) === (cur ?? null)));
  const sizes = useQuery({
    queryKey: ["shopping-sizes", it.id], retry: false, enabled: !!it.group && !it.group.pending,
    queryFn: () => send<SizeOpt[]>(`/api/shopping-list/${it.id}/sizes`),
  });
  const showSizes = !renamed && unit === "adet" && (sizes.data?.length ?? 0) > 0;

  const step = (dir: 1 | -1) => setQtyS(fmtQty(stepQty(qty, unit, dir)));
  const pickUnit = (u: Unit) => { setQtyS(fmtQty(convertQty(okQty ? qty : Number.NaN, unit, u))); setUnit(u); };
  const pickSug = (s: Suggestion) => { setText(s.text); setPicked(s); setSugOpen(false); setActive(-1); };

  const save = () => {
    const p: ItemPatch = {};
    const t = text.trim();
    if (t && renamed) {
      p.text = t;
      if (picked && picked.text === t && picked.group_key) p.group_key = picked.group_key;
    } else if (showSizes && size !== cur) p.group_key = size;
    if (okQty && (qty !== (it.qty ?? 1) || unit !== unit0)) { p.qty = qty; p.unit = unit; }
    if (!Object.keys(p).length) return onClose();
    onSave(p);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!sugOpen || !sugItems.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % sugItems.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a <= 0 ? sugItems.length - 1 : a - 1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pickSug(sugItems[active]!); }
  };

  return (
    <Modal title={hint && hint.reason !== "pending" ? "Ürünü düzelt" : "Ürünü düzenle"} onClose={onClose} footer={<>
      <button type="button" className="btn-danger" onClick={onRemove} disabled={busy} data-testid="listeden-cikar"><Trash2 size={16} aria-hidden /> Listeden çıkar</button>
      <div className="flex-1" />
      <button type="button" className="btn-ghost hidden sm:inline-flex" onClick={onClose}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={busy || !text.trim() || !okQty} onClick={save}>Kaydet</button>
    </>}>
      <div className="space-y-4">
        {hint && (
          <p className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" data-testid="duzelt-ipucu">
            <Info size={16} className="mt-0.5 shrink-0" aria-hidden />{hint.hint}</p>
        )}
        <div className="relative">
          <label className="label" htmlFor="sd-text">Ürün</label>
          <input id="sd-text" className="input" value={text} maxLength={120} autoComplete="off"
            onChange={(e) => { setText(e.target.value); setPicked(null); setSugOpen(true); setActive(-1); }}
            onFocus={() => setSugOpen(true)} onKeyDown={onKey}
            {...(autofill ? { role: "combobox", "aria-expanded": sugOpen && sugItems.length > 0, "aria-controls": lid, "aria-autocomplete": "list" as const,
              "aria-activedescendant": active >= 0 ? `${lid}-${active}` : undefined } : {})} />
          {autofill && sugOpen && sugItems.length > 0 && text.trim().length >= 2 && (
            <div className="mt-2">
              <p className="mb-1 text-xs text-stone-500 dark:text-stone-400">Bunu mu demek istedin?</p>
              <SuggestList id={lid} items={sugItems} active={active} onPick={pickSug} className="max-h-56 shadow-none" />
            </div>
          )}
        </div>

        <div>
          <span className="label" id="sd-qty-l">Miktar</span>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center rounded-lg border border-stone-300 dark:border-stone-700" role="group" aria-labelledby="sd-qty-l">
              <button type="button" className="btn-ghost rounded-r-none px-2.5 py-2" aria-label="Azalt" onClick={() => step(-1)}><Minus size={16} /></button>
              <input className="w-16 border-x border-stone-200 bg-transparent py-1.5 text-center text-base tabular-nums outline-none focus:bg-blue-50/50 sm:text-sm dark:border-stone-700 dark:focus:bg-blue-950/30"
                inputMode="decimal" value={qtyS} onChange={(e) => setQtyS(e.target.value)} aria-label={`Miktar (${unit})`} aria-invalid={!okQty} />
              <button type="button" className="btn-ghost rounded-l-none px-2.5 py-2" aria-label="Artır" onClick={() => step(1)}><Plus size={16} /></button>
            </div>
            <div className="flex rounded-lg bg-stone-100 p-1 dark:bg-stone-800" role="radiogroup" aria-label="Birim">
              {UNITS.map((u) => (
                <button key={u} type="button" role="radio" aria-checked={unit === u} onClick={() => pickUnit(u)}
                  className={cx("min-w-10 rounded-md px-2 py-1 text-sm font-medium", unit === u ? "bg-white text-stone-900 shadow-sm dark:bg-stone-600 dark:text-white" : "text-stone-600 dark:text-stone-300")}>{u}</button>
              ))}
            </div>
          </div>
          {!okQty && <p className="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">Geçerli bir miktar yaz (ör. 2,5).</p>}
        </div>

        {showSizes && (
          <div>
            <span className="label">Boy</span>
            <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700" data-testid="boy-listesi">
              {sizes.data!.map((o) => (
                <li key={o.key}>
                  <button type="button" onClick={() => setSize(o.key)} aria-pressed={o.key === size}
                    className={cx("flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", o.key === size && "bg-green-50/70 dark:bg-green-950/30")}>
                    <span className="min-w-0 flex-1 font-medium">{o.size_label}{o.key === size && <Check size={15} className="ml-1.5 inline text-green-700 dark:text-green-400" aria-label="seçili" />}</span>
                    <span className="shrink-0 text-right text-sm text-stone-600 tabular-nums dark:text-stone-300">
                      {o.price != null ? <>{o.chain ? `${chainName(o.chain)} ` : ""}{money(o.price)}{o.unit_price_per != null && o.per_unit ? <span className="block text-xs text-stone-500 dark:text-stone-400">{money(o.unit_price_per)}/{o.per_unit}</span> : null}</> : <span className="text-stone-400">fiyat yok</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  );
}
