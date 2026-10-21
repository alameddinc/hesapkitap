import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type Rule } from "../api";
import CategoryPicker from "../components/CategoryPicker";
import ItemRules from "../components/ItemRules";
import { useMeta, useSave } from "../components/hooks";
import { useUI } from "../ui";

function RuleRow({ r, cats }: { r: Rule; cats: string[] }) {
  const ui = useUI();
  const [pattern, setPattern] = useState(r.pattern);
  const [category, setCategory] = useState(r.category);
  const dirty = pattern !== r.pattern || category !== r.category;
  const save = useSave(() => api.patchRule(r.id, { pattern, category }), () => ui.toast("Kural kaydedildi, işlemler güncellendi"));
  const del = useSave(() => api.deleteRule(r.id), () => ui.toast("Kural silindi"));
  return (
    <li className="flex flex-col gap-2 py-2 sm:flex-row sm:items-center">
      <input className="input font-mono text-base sm:flex-1 sm:text-xs" value={pattern} onChange={(e) => setPattern(e.target.value)} aria-label="Pattern" />
      <div className="flex gap-2 sm:w-72">
        <div className="flex-1"><CategoryPicker value={category} onChange={setCategory} options={cats} /></div>
        {dirty && <button className="btn-primary" onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}
        <button className="btn-danger p-2" aria-label="Sil" onClick={() => confirm("Kural silinsin mi?") && del.mutate(undefined)}>
          <Trash2 size={16} />
        </button>
      </div>
    </li>
  );
}

export default function Rules() {
  const ui = useUI();
  const meta = useMeta();
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const [np, setNp] = useState("");
  const [nc, setNc] = useState("");
  const [sample, setSample] = useState("");
  const test = useQuery({
    queryKey: ["rule-test", np, sample],
    queryFn: () => api.testRule(np, sample),
    enabled: !!np && !!sample,
    retry: false,
  });
  const add = useSave(() => api.createRule({ pattern: np, category: nc, priority: 10 }), () => {
    setNp("");
    setNc("");
    ui.toast("Kural eklendi, işlemler güncellendi");
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Kategori kuralları</h1>
        <p className="mt-1 text-sm text-stone-500">
          İşlem açıklaması büyük harfe ve Türkçe karaktersiz hale çevrilip (İSTANBUL → ISTANBUL) bu regex'lerle eşleştirilir.
          Yukarıdaki kural önce çalışır. Elle kategori verdiğin işlemler etkilenmez.
        </p>
      </div>

      <ItemRules />

      <section className="card space-y-2 p-3 sm:p-4">
        <h2 className="font-semibold">Yeni kural</h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="input font-mono text-base sm:flex-1 sm:text-xs" placeholder="ör. NETFLIX|DISNEY" value={np} onChange={(e) => setNp(e.target.value)} />
          <div className="sm:w-56"><CategoryPicker value={nc} onChange={setNc} options={meta.data?.categories ?? []} placeholder="Kategori" /></div>
          <button className="btn-primary justify-center" disabled={!np || !nc}
            onClick={() => add.mutate(undefined, { onError: (e) => ui.toast(e.message) })}><Plus size={16} /> Ekle</button>
        </div>
        <input className="input" placeholder="Test için örnek açıklama yaz: IYZICO/SPOTIFY.COM/ISTANBUL" value={sample} onChange={(e) => setSample(e.target.value)} />
        {test.data && (
          <p className="text-xs">
            <code>{test.data.normalized}</code> → {test.data.match ? <span className="text-green-700">eşleşti ✓</span> : <span className="text-red-600">eşleşmedi</span>}
          </p>
        )}
        {test.error && <p className="text-xs text-red-600">{(test.error as Error).message}</p>}
      </section>

      <section className="card p-3 sm:p-4">
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {rules.data?.map((r) => <RuleRow key={`${r.id}-${r.pattern}-${r.category}`} r={r} cats={meta.data?.categories ?? []} />)}
        </ul>
      </section>
    </div>
  );
}
