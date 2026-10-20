import { Landmark, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

type Key = "date" | "description" | "amount" | "debit" | "credit" | "balance";
interface Preview {
  header_row: number;
  columns: string[];
  mapping: Record<Key, number | null>;
  profile_saved: boolean;
  total: number;
  counts: Record<string, number>;
  sample: { date: string; description: string; amount: number; type: string }[];
}

const LABEL: Record<string, string> = {
  gelir: "Gelir", gider: "Gider", kart_odemesi: "Kart ödemesi", virman: "Virman (atlanır)", tekrar: "Zaten var",
};
const FIELDS: { key: Key; label: string }[] = [
  { key: "date", label: "Tarih" }, { key: "description", label: "Açıklama" }, { key: "amount", label: "Tutar (±)" },
  { key: "debit", label: "Borç / çıkan" }, { key: "credit", label: "Alacak / giren" }, { key: "balance", label: "Bakiye" },
];

async function postForm<T>(url: string, file: File, extra: Record<string, string> = {}): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  const r = await fetch(url, { method: "POST", body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
}

/** Vadesiz hesap dökümü (Excel/CSV) içe aktarma: sütun tahmini → önizleme → içe aktar. */
export default function BankImport() {
  const ui = useUI();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const preview = async (f: File, mapping?: Preview["mapping"], headerRow?: number) => {
    setBusy(true);
    setErr(null);
    try {
      setPv(await postForm<Preview>("/api/bank-import/preview", f,
        mapping ? { mapping: JSON.stringify(mapping), header_row: String(headerRow ?? 0) } : {}));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const run = useSave(() => postForm<Record<string, number>>("/api/bank-import", file!, {
    mapping: JSON.stringify(pv!.mapping), header_row: String(pv!.header_row),
  }), (r) => {
    const parts = [r.gelir && `${r.gelir} gelir`, r.gider && `${r.gider} gider`, r.maas && `${r.maas} maaş onaylandı`,
      r.odendi_isaretlenen && `${r.odendi_isaretlenen} ekstre ödendi`, r.eslesen_elle && `${r.eslesen_elle} elle girilenle eşleşti`,
      r.tekrar && `${r.tekrar} zaten vardı`].filter(Boolean);
    ui.toast(parts.length ? parts.join(" · ") : "Yeni hareket yok");
    setPv(null);
    setFile(null);
  });
  const setCol = (k: Key, v: string) => {
    if (!pv || !file) return;
    const mapping = { ...pv.mapping, [k]: v === "" ? null : Number(v) };
    if (k === "amount" && v !== "") Object.assign(mapping, { debit: null, credit: null });
    if ((k === "debit" || k === "credit") && v !== "") mapping.amount = null;
    preview(file, mapping, pv.header_row);
  };

  return (
    <section className="card space-y-3 p-4">
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Landmark size={18} /> Hesap hareketleri</h2>
        <p className="text-xs text-stone-500">
          Vadesiz hesabının dökümü (Excel ya da CSV, internet şubesi → hesap hareketleri). Maaş sabit kaleme bağlanır,
          kredi kartı ödemeleri ilgili ekstreyi "ödendi" yapar, virmanlar atlanır, aynı hareket iki kez eklenmez.
        </p>
      </div>
      <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <Landmark size={16} />} Dosya seç (.xlsx, .xls, .csv)
      </button>
      <input ref={input} type="file" accept=".xlsx,.xls,.csv,text/csv" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { setFile(f); preview(f); } }} />
      {err && <p className="text-sm text-red-600">{err}</p>}

      {pv && file && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {FIELDS.map((f) => (
              <label key={f.key} className="text-xs">
                <span className="label">{f.label}</span>
                <select className="input py-1" value={pv.mapping[f.key] ?? ""} onChange={(e) => setCol(f.key, e.target.value)}>
                  <option value="">—</option>
                  {pv.columns.map((c, i) => <option key={i} value={i}>{c || `Sütun ${i + 1}`}</option>)}
                </select>
              </label>
            ))}
          </div>
          {pv.profile_saved && <p className="text-xs text-green-700 dark:text-green-400">Bu bankanın biçimi hatırlandı.</p>}
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(pv.counts).map(([k, n]) => (
              <span key={k} className={cx("rounded-full px-2 py-0.5", k === "tekrar" || k === "virman" ? "bg-stone-100 dark:bg-stone-800" : "bg-blue-50 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300")}>
                {LABEL[k] ?? k}: {n}
              </span>
            ))}
          </div>
          <div className="max-h-64 overflow-auto rounded-lg border border-stone-200 dark:border-stone-700">
            <table className="w-full text-xs whitespace-nowrap [overflow-wrap:normal]">
              <tbody>
                {pv.sample.map((r, i) => (
                  <tr key={i} className="border-t border-stone-100 first:border-0 dark:border-stone-800">
                    <td className="px-2 py-1 text-stone-500">{dateTR(r.date)}</td>
                    <td className="max-w-32 truncate px-2 py-1 sm:max-w-64">{r.description}</td>
                    <td className={cx("px-2 py-1 text-right tabular-nums", r.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(r.amount)}</td>
                    <td className="px-2 py-1 text-stone-500">{LABEL[r.type] ?? r.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={!pv.total || run.isPending}
              onClick={() => run.mutate(undefined, { onError: (e) => setErr(e.message) })}>
              {pv.total} hareketi içe aktar
            </button>
            <button className="btn-ghost" onClick={() => { setPv(null); setFile(null); }}>Vazgeç</button>
          </div>
        </div>
      )}
    </section>
  );
}
