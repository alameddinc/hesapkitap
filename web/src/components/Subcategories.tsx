import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, FolderTree, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type Subcategory } from "../api";
import { ask } from "../lib/ask";
import { useUI } from "../ui";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";
import { useSubcategories } from "./SubcategoryPicker";

function Item({ s, first, last, onMove, canEdit }: {
  s: Subcategory; first: boolean; last: boolean; onMove: (d: -1 | 1) => void; canEdit: boolean;
}) {
  const ui = useUI();
  const [name, setName] = useState(s.name);
  const dirty = name.trim() !== s.name && name.trim() !== "";
  const save = useSave(() => api.renameSubcategory(s.id, name.trim()),
    (r) => ui.toast(r.updated ? `Yeniden adlandırıldı · ${r.updated} işlem güncellendi` : "Yeniden adlandırıldı"));
  const del = useSave(() => api.deleteSubcategory(s.id), (r) => ui.toast(r.cleared ? `Silindi · ${r.cleared} işlemden kaldırıldı` : "Silindi"));
  const remove = async () => {
    const msg = s.count ? `“${s.category} › ${s.name}” silinsin mi?\n\n${s.count} işlemden kaldırılır; ana kategorileri (${s.category}) değişmez.`
      : `“${s.category} › ${s.name}” silinsin mi?`;
    if (await ask(msg)) del.mutate(undefined, { onError: (e) => ui.error(e.message) });
  };
  return (
    <li className="flex items-center gap-2 py-1.5">
      <input className="input min-w-0 flex-1" value={name} disabled={!canEdit} aria-label={`${s.category} alt kategorisi`}
        onChange={(e) => setName(e.target.value)} maxLength={40}
        onKeyDown={(e) => { if (e.key === "Enter" && dirty) save.mutate(undefined, { onError: (er) => ui.error(er.message) }); }} />
      <span className="w-16 shrink-0 text-right text-xs text-stone-500 tabular-nums">{s.count} işlem</span>
      {canEdit && <>
        {dirty && <button className="btn-primary" onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>Kaydet</button>}
        <button className="btn-ghost p-1.5" aria-label="Yukarı taşı" disabled={first} onClick={() => onMove(-1)}><ArrowUp size={14} /></button>
        <button className="btn-ghost p-1.5" aria-label="Aşağı taşı" disabled={last} onClick={() => onMove(1)}><ArrowDown size={14} /></button>
        <button className="btn-danger p-2" aria-label={`${s.name} alt kategorisini sil`} disabled={del.isPending} onClick={() => void remove()}>
          <Trash2 size={16} /></button>
      </>}
    </li>
  );
}

/** Alt kategoriler: ana kategoriye göre ekle / yeniden adlandır (işlemlere yansır) / sil / sırala. Eski sunucuda yok. */
export default function Subcategories() {
  const ui = useUI();
  const meta = useMeta();
  const subs = useSubcategories();
  const q = useQuery({ queryKey: ["subcategories"], queryFn: api.subcategories, enabled: subs.on });
  const [cat, setCat] = useState("");
  const [name, setName] = useState("");
  const add = useSave(() => api.addSubcategory(cat, name.trim()), (r) => {
    setName("");
    ui.toast(r.created ? `${r.category} › ${r.name} eklendi` : `${r.category} altında “${r.name}” zaten var`);
  });
  const order = useSave((a: { category: string; ids: number[] }) => api.orderSubcategories(a.category, a.ids));
  if (!subs.on) return null;
  const canEdit = q.data?.can_edit ?? subs.editable;
  const groups = new Map<string, Subcategory[]>();
  for (const s of q.data?.items ?? []) groups.set(s.category, [...(groups.get(s.category) ?? []), s]);
  const move = (list: Subcategory[], i: number, d: -1 | 1) => {
    const ids = list.map((x) => x.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    order.mutate({ category: list[0].category, ids }, { onError: (e) => ui.error(e.message) });
  };
  const cats = (meta.data?.categories ?? []).filter((c) => c !== "Kategorisiz");
  return (
    <section className="card space-y-3 p-3 sm:p-4" aria-labelledby="subcat-h">
      <div className="flex items-start gap-2">
        <FolderTree size={18} className="mt-0.5 shrink-0 text-blue-600 dark:text-blue-400" aria-hidden="true" />
        <div>
          <h2 id="subcat-h" className="font-display font-semibold tracking-tight">Alt kategoriler</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Ana kategorinin altında ayrıntı: Market & Gıda › Temizlik. Bütçe ve istatistik ana kategoriyle sayılır; istatistikte ana
            kategorinin içinde alt kategori kırılımı görünür. Aynı ad farklı ana kategorilerin altında olabilir.
          </p>
        </div>
      </div>
      {canEdit && (
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="sm:w-56"><CategoryPicker value={cat} onChange={setCat} options={cats} placeholder="Ana kategori" /></div>
          <input className="input sm:flex-1" placeholder="Alt kategori adı (ör. Temizlik)" value={name} maxLength={40}
            onChange={(e) => setName(e.target.value)} aria-label="Alt kategori adı"
            onKeyDown={(e) => { if (e.key === "Enter" && cat && name.trim()) add.mutate(undefined, { onError: (er) => ui.error(er.message) }); }} />
          <button className="btn-primary justify-center" disabled={!cat || !name.trim() || add.isPending}
            onClick={() => add.mutate(undefined, { onError: (e) => ui.error(e.message) })}><Plus size={16} /> Ekle</button>
        </div>
      )}
      {q.error ? <p className="text-sm text-red-600 dark:text-red-400">{(q.error as Error).message}</p>
        : !groups.size ? (
          <p className="rounded-lg border border-dashed border-stone-300 px-3 py-4 text-center text-sm text-stone-500 dark:border-stone-700 dark:text-stone-400">
            Henüz alt kategori yok{canEdit ? " — ana kategoriyi seçip adını yaz." : "."}
          </p>
        ) : (
          <div className="space-y-3">
            {[...groups].map(([c, list]) => (
              <div key={c}>
                <h3 className="text-sm font-semibold">{c}</h3>
                <ul>
                  {list.map((s, i) => (
                    <Item key={`${s.id}-${s.name}`} s={s} first={i === 0} last={i === list.length - 1} canEdit={canEdit}
                      onMove={(d) => move(list, i, d)} />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
    </section>
  );
}
