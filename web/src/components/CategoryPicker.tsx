import { Check, ChevronDown, Plus } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cx, fold } from "../format";


/** Kategori seçici: dokununca tüm liste açılır, yazdıkça filtreler, listede yoksa yeni kategori eklenir. */
export default function CategoryPicker({ id, value, onChange, options, placeholder = "Kategori seç" }: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  const filtered = useMemo(() => {
    const q = fold(query.trim());
    const list = options.filter((o) => o !== "Kategorisiz");
    return q ? list.filter((o) => fold(o).includes(q)) : list;
  }, [options, query]);
  const canCreate = query.trim() && !options.some((o) => fold(o) === fold(query.trim()));
  const items = [...filtered, ...(canCreate ? [`__new__${query.trim()}`] : [])];

  const pick = (v: string) => {
    onChange(v.startsWith("__new__") ? v.slice(7) : v);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <div className="relative">
        <input
          id={id} ref={input} role="combobox" aria-expanded={open} aria-controls={listId} autoComplete="off"
          className="input pr-9"
          value={open ? query : value === "Kategorisiz" ? "" : value}
          placeholder={open && value && value !== "Kategorisiz" ? value : placeholder}
          onFocus={() => {
            setOpen(true); setQuery(""); setActive(0);
            // modal içinde açılan listenin görünür olması için alanı yukarı kaydır
            setTimeout(() => box.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
          }}
          onChange={(e) => { setQuery(e.target.value); setActive(0); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && open && items[active]) { e.preventDefault(); pick(items[active]); }
            else if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); }
          }}
        />
        <button type="button" tabIndex={-1} aria-label="Kategorileri göster"
          className="absolute top-1/2 right-2 -translate-y-1/2 p-1 text-stone-400"
          onClick={() => { setOpen((o) => !o); input.current?.focus(); }}>
          <ChevronDown size={16} className={cx("transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && (
        <ul id={listId} role="listbox"
          className="mt-1 max-h-56 w-full overflow-y-auto overscroll-contain rounded-lg border border-stone-200 bg-white py-1 shadow-lg dark:border-stone-700 dark:bg-stone-900">
          {items.map((o, i) => {
            const isNew = o.startsWith("__new__");
            const label = isNew ? o.slice(7) : o;
            return (
              <li key={o} role="option" aria-selected={label === value}
                onPointerDown={(e) => { e.preventDefault(); pick(o); }}
                onMouseEnter={() => setActive(i)}
                className={cx("flex cursor-pointer items-center gap-2 px-3 py-2 text-sm",
                  i === active && "bg-stone-100 dark:bg-stone-800", isNew && "text-blue-600 dark:text-blue-400")}>
                {isNew ? <Plus size={14} /> : <Check size={14} className={label === value ? "opacity-100" : "opacity-0"} />}
                {isNew ? <>Yeni kategori: <b>{label}</b></> : label}
              </li>
            );
          })}
          {!items.length && <li className="px-3 py-2 text-sm text-stone-500">Kategori yok — yazarak ekle</li>}
        </ul>
      )}
    </div>
  );
}
