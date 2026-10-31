import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { ArrowLeftRight, Plus, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errStatus, type MatchRule, type Rule } from "../api";
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
        {dirty && <button className="btn-primary" onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>Kaydet</button>}
        <button className="btn-danger p-2" aria-label="Sil" disabled={del.isPending} onClick={() => void ask("Kural silinsin mi?").then((ok) => { if (ok) del.mutate(undefined); })}>
          <Trash2 size={16} />
        </button>
      </div>
    </li>
  );
}

/** Öğrenilen eşleşmeler: dökümdeki/ekstredeki kalıp ↔ elle yazdığın açıklama. Ortak defter üyesinde (403) görünmez. */
function LearnedMatches() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["match-rules"], queryFn: api.matchRules, retry: false });
  const del = useSave((id: number) => api.deleteMatchRule(id), () => ui.toast("Öğrenilen eşleşme silindi"));
  if (q.isPending || (q.error && errStatus(q.error) === 403)) return null;
  const rules = q.data ?? [];
  const remove = async (r: MatchRule) => {
    if (!(await ask(`“${r.bank_label} ↔ ${r.entry_label}” eşleşmesi unutulsun mu?\n\nŞimdiye kadar birleşen kayıtlar olduğu gibi kalır; bundan sonra bu ikisi yeniden sorulur.`))) return;
    del.mutate(r.id, { onError: (e) => ui.error(e.message) });
  };
  return (
    <section className="card p-3 sm:p-4" aria-labelledby="learned-h">
      <div className="flex items-start gap-2">
        <Sparkles size={18} className="mt-0.5 shrink-0 text-blue-600 dark:text-blue-400" aria-hidden="true" />
        <div>
          <h2 id="learned-h" className="font-display font-semibold tracking-tight">Öğrenilen eşleşmeler</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Elle girdiğin bir kaydı dökümdeki ya da ekstredeki satırla eşleştirdikçe öğrenilir; sonra aynı ikili sormadan birleşir, iki kez sayılmaz.
          </p>
        </div>
      </div>
      {q.error ? (
        <p className="mt-3 text-sm text-red-600 dark:text-red-400">{(q.error as Error).message}</p>
      ) : !rules.length ? (
        <p className="mt-3 rounded-lg border border-dashed border-stone-300 px-3 py-4 text-center text-sm text-stone-500 dark:border-stone-700 dark:text-stone-400">
          Bir dökümdeki kalıbı aynı elle kayıtla iki kez eşleştirince burada kural olur.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-stone-100 dark:divide-stone-800">
          {rules.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                  <code className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-xs dark:bg-stone-800" title={r.pattern}>{r.bank_label}</code>
                  <ArrowLeftRight size={13} className="text-stone-400" aria-label="ile eşleşir" />
                  <span className="font-medium">{r.entry_label}</span>
                  {r.category && <span className="text-xs text-stone-500">· {r.category}</span>}
                </div>
                <div className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
                  {r.source === "dokum" ? "Hesap dökümü" : "Kart ekstresi"} ·{" "}
                  {r.used ? `${r.used} kez kendiliğinden eşleşti` : `${r.hits} kez onayladın · sıradaki kendiliğinden eşleşir`}
                </div>
              </div>
              <button className="btn-danger p-2" aria-label={`${r.bank_label} ↔ ${r.entry_label} eşleşmesini sil`}
                disabled={del.isPending} onClick={() => remove(r)}><Trash2 size={16} /></button>
            </li>
          ))}
        </ul>
      )}
    </section>
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

      <LearnedMatches />

      <section className="card space-y-2 p-3 sm:p-4">
        <h2 className="font-semibold">Yeni kural</h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="input font-mono text-base sm:flex-1 sm:text-xs" placeholder="ör. NETFLIX|DISNEY" value={np} onChange={(e) => setNp(e.target.value)} />
          <div className="sm:w-56"><CategoryPicker value={nc} onChange={setNc} options={meta.data?.categories ?? []} placeholder="Kategori" /></div>
          <button className="btn-primary justify-center" disabled={!np || !nc}
            onClick={() => add.mutate(undefined, { onError: (e) => ui.error(e.message) })}><Plus size={16} /> Ekle</button>
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
