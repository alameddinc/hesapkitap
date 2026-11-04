import { X } from "lucide-react";
import CategoryPicker from "./CategoryPicker";
import { useMeta } from "./hooks";

/** Sunucu alt kategoriyi biliyor mu (meta.subcategories var; eski sunucuda alan yok → hiçbir alt kategori arayüzü yok). */
export function useSubcategories() {
  const meta = useMeta().data;
  return { on: !!meta && "subcategories" in meta, map: meta?.subcategories ?? {}, editable: meta?.subcategories_editable !== false };
}

/**
 * İşlem düzenlemede ana kategorinin altındaki alt kategori. Ana kategori yoksa görünmez. Listede olmayan ad yetkili
 * kişide yazılarak eklenir (sunucu kayıtla birlikte listeye ekler); "Görür ve ekler" üye yalnız listeden seçer.
 */
export default function SubcategoryPicker({ id, category, value, onChange }: {
  id?: string; category: string; value: string; onChange: (v: string) => void;
}) {
  const s = useSubcategories();
  if (!s.on || !category || category === "Kategorisiz") return null;
  const options = s.map[category] ?? [];
  if (!options.length && !s.editable) return null;
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="label" htmlFor={id}>Alt kategori <span className="font-normal text-stone-400">(isteğe bağlı)</span></label>
        {value && (
          <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={() => onChange("")} aria-label="Alt kategoriyi kaldır">
            <X size={12} /> Kaldır
          </button>
        )}
      </div>
      <CategoryPicker id={id} value={value} onChange={onChange} options={options} noun="alt kategori" allowCreate={s.editable}
        placeholder={options.length ? `${category} altında seç${s.editable ? " veya yaz" : ""}` : "Yazıp ekle"} />
    </div>
  );
}
