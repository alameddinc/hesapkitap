import { FileText, Trash2 } from "lucide-react";
import { ask } from "../lib/ask";
import { useState } from "react";
import { parseAmount } from "../format";
import { api, type BillDraft } from "../api";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";
import Modal from "./Modal";

const BILL_CATS = ["Elektrik", "Doğalgaz", "Su", "İnternet & TV", "Telefon", "Kira & Aidat", "Faturalar"];

export default function BillForm({ draft, onClose, onSaved }: { draft: BillDraft; onClose: () => void; onSaved?: (m: string) => void }) {
  const meta = useMeta();
  const [provider, setProvider] = useState(draft.provider ?? "");
  const [amount, setAmount] = useState(draft.amount != null ? String(draft.amount) : "");
  const [due, setDue] = useState(draft.due_date ?? "");
  const [category, setCategory] = useState(draft.category ?? "Faturalar");
  const [description, setDescription] = useState(draft.description ?? "");
  const [note, setNote] = useState(draft.note ?? "");
  const [err, setErr] = useState<string | null>(draft.error ? `Otomatik okunamadı: ${draft.error}. Elle doldur.` : null);

  const save = useSave(async () => {
    const amt = parseAmount(amount);
    if (!provider.trim() || !(amt >= 0) || !amount || !due) throw new Error("Kurum, tutar ve son ödeme tarihi gerekli");
    const body = { provider: provider.trim(), amount: amt, due_date: due, category, description: description || null, note: note || null };
    if (draft.id) return api.patchBill(draft.id, body);
    return api.createBill({ ...body, file_name: draft.file_name ?? null });
  }, () => { onSaved?.("Fatura kaydedildi"); onClose(); });
  const del = useSave(() => api.deleteBill(draft.id!), () => { onSaved?.("Fatura silindi"); onClose(); });

  const cats = [...BILL_CATS, ...(meta.data?.categories ?? []).filter((c) => !BILL_CATS.includes(c))];

  return (
    <Modal title={draft.id ? "Faturayı düzenle" : "Fatura ekle"} onClose={onClose} footer={
      <>
        {draft.id && <button className="btn-danger" onClick={() => void ask("Fatura silinsin mi?").then((ok) => { if (ok) del.mutate(undefined); })}><Trash2 size={16} /> Sil</button>}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending}
          onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        {draft.file_name && (
          <div className="flex items-center gap-2 rounded-lg bg-stone-100 p-2 text-xs dark:bg-stone-800">
            <FileText size={14} /> Dosya eklendi
            {draft.confidence && draft.confidence !== "yüksek" && <span className="text-amber-600">· okuma güveni {draft.confidence}, kontrol et</span>}
          </div>
        )}
        <div>
          <label className="label" htmlFor="b-prov">Kurum</label>
          <input id="b-prov" className="input" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="ör. Enerjisa, Türk Telekom" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="b-amt">Tutar (₺)</label>
            <input id="b-amt" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="b-due">Son ödeme</label>
            <input id="b-due" type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="b-cat">Tür</label>
          <CategoryPicker id="b-cat" value={category} onChange={setCategory} options={cats} />
        </div>
        <div>
          <label className="label" htmlFor="b-desc">Dönem / abone no</label>
          <input id="b-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="b-note">Not</label>
          <input id="b-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="ör. otomatik ödeme talimatı var" />
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
      </div>
    </Modal>
  );
}
