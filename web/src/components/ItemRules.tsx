import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { errorText } from "../lib/apiError";
import { Plus, Scissors, Trash2 } from "lucide-react";
import { useState } from "react";
import { useUI } from "../ui";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";

interface ItemRule { id: number; keywords: string; category: string }

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errorText(j, r.status));
  return j as T;
};

function RuleRow({ r, cats }: { r: ItemRule; cats: string[] }) {
  const ui = useUI();
  const [kw, setKw] = useState(r.keywords);
  const [cat, setCat] = useState(r.category);
  const dirty = kw !== r.keywords || cat !== r.category;
  const save = useSave(() => json("PUT", `/api/item-rules/${r.id}`, { keywords: kw, category: cat }), () => ui.toast("Kalem kuralı kaydedildi"));
  const del = useSave(() => json("DELETE", `/api/item-rules/${r.id}`), () => ui.toast("Silindi"));
  return (
    <li className="space-y-2 py-3">
      <div className="flex gap-2">
        <div className="flex-1"><CategoryPicker value={cat} onChange={setCat} options={cats} /></div>
        {dirty && <button className="btn-primary" onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}
        <button className="btn-danger p-2" aria-label="Sil" onClick={() => void ask("Kalem kuralı silinsin mi?").then((ok) => { if (ok) del.mutate(undefined); })}>
          <Trash2 size={16} />
        </button>
      </div>
      <textarea className="input min-h-24 text-base sm:text-sm" value={kw} onChange={(e) => setKw(e.target.value)}
        aria-label="Anahtar kelimeler (virgülle)" />
    </li>
  );
}

/** Fişteki satırları kategoriye ayıran kurallar: "sigara, marlboro, …" → Sigara. */
export default function ItemRules() {
  const ui = useUI();
  const meta = useMeta();
  const rules = useQuery({ queryKey: ["item-rules"], queryFn: () => json<ItemRule[]>("GET", "/api/item-rules") });
  const [kw, setKw] = useState("");
  const [cat, setCat] = useState("");
  const [sample, setSample] = useState("");
  const test = useQuery({
    queryKey: ["item-rule-test", sample, rules.data],
    queryFn: () => json<{ category: string | null }>("POST", "/api/item-rules/test", { text: sample }),
    enabled: sample.trim().length > 1,
  });
  const add = useSave(() => json("POST", "/api/item-rules", { keywords: kw, category: cat }), () => {
    ui.toast("Kalem kuralı eklendi"); setKw(""); setCat("");
  });
  const cats = meta.data?.categories ?? [];
  return (
    <section className="card space-y-3 p-3 sm:p-4">
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Scissors size={18} /> Kalem kuralları (fişi böl)</h2>
        <p className="mt-1 text-sm text-stone-500">
          Fişteki bir satırda bu kelimelerden biri geçerse o satır harcamadan ayrılıp bu kategoriye yazılır; geri kalanı
          işyerinin kategorisinde kalır (ör. market fişindeki sigara). Kelimeye uymayan kısaltmaları ("MRLB TCH") fişi
          okuyan yapay zekâ da bu kategorilere göre ayırır. Fiş yoksa harcamayı açıp "Böl" ile elle ayırabilirsin.
        </p>
      </div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {rules.data?.map((r) => <RuleRow key={`${r.id}-${r.keywords}-${r.category}`} r={r} cats={cats} />)}
        {rules.data?.length === 0 && <li className="py-2 text-sm text-stone-500">Henüz kalem kuralı yok.</li>}
      </ul>
      <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
        <div className="text-sm font-medium">Yeni kalem kuralı</div>
        <CategoryPicker value={cat} onChange={setCat} options={cats} placeholder="Kategori (ör. Alkol)" />
        <input className="input" placeholder="Kelimeler, virgülle: bira, efes, rakı, şarap" value={kw} onChange={(e) => setKw(e.target.value)} />
        <button className="btn-primary" disabled={!kw.trim() || !cat.trim() || add.isPending}
          onClick={() => add.mutate(undefined, { onError: (e) => ui.toast(e.message) })}><Plus size={16} /> Ekle</button>
      </div>
      <input className="input" placeholder="Dene: fiş satırı yaz (ör. PARLIAMENT RESERVE)" value={sample} onChange={(e) => setSample(e.target.value)} />
      {sample.trim().length > 1 && test.data && (
        <p className="text-xs">{test.data.category ? <span className="text-green-700 dark:text-green-400">→ {test.data.category} ✓</span>
          : <span className="text-stone-500">hiçbir kurala uymadı (işyerinin kategorisinde kalır)</span>}</p>
      )}
    </section>
  );
}
