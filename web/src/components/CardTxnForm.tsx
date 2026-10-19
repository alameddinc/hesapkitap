import { ArrowDownRight, Ban, EyeOff, Repeat, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api, type Row } from "../api";
import { cx, dateTR, KIND_LABEL, tl } from "../format";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";
import { DowngradeForm } from "../pages/Savings";
import Modal from "./Modal";

/** Ekstreden gelen işlemi düzenle: kategori, tip (faiz → harcama gibi), not, gizle/geri al. */
export default function CardTxnForm({ row, onClose, onSaved }: { row: Row; onClose: () => void; onSaved?: (m: string) => void }) {
  const meta = useMeta();
  const [category, setCategory] = useState(row.category);
  const [kind, setKind] = useState(row.kind);
  const [description, setDescription] = useState(row.description);
  const [note, setNote] = useState(row.note ?? "");
  const [makeRule, setMakeRule] = useState(true);

  const save = useSave((body: Record<string, unknown>) => api.patchCard(row.id, body), (r) => {
    onSaved?.(r.rule ? `Kaydedildi · kural: ${r.rule}` : "Kaydedildi");
    onClose();
  });

  const categoryChanged = category !== row.category && category !== "";
  const [downgrade, setDowngrade] = useState(false);
  const subSave = useSave((status: string) => fetch("/api/subscriptions", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ merchant: row.merchant, status }),
  }).then((r) => { if (!r.ok) throw new Error("Kaydedilemedi"); }), () => { onSaved?.("Abonelik güncellendi"); onClose(); });
  const submit = () => {
    const body: Record<string, unknown> = {};
    if (categoryChanged) Object.assign(body, { category, create_rule: makeRule });
    if (kind !== row.kind) body.txn_type = kind;
    if (description !== row.description) body.description = description;
    if (note !== (row.note ?? "")) body.note = note;
    if (Object.keys(body).length === 0) return onClose();
    save.mutate(body);
  };

  return (
    <Modal title="Kart işlemi" onClose={onClose} footer={
      <>
        <button className={row.deleted ? "btn-ghost" : "btn-danger"} onClick={() => save.mutate({ is_deleted: !row.deleted })}>
          {row.deleted ? <><RotateCcw size={16} /> Geri al</> : <><EyeOff size={16} /> Sil</>}
        </button>
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" onClick={submit} disabled={save.isPending}>Kaydet</button>
      </>
    }>
      <div className="space-y-3">
        <div className="rounded-lg bg-stone-100 p-3 text-sm dark:bg-stone-800">
          <div className="flex justify-between gap-2">
            <span className="font-medium">{row.description}</span>
            <span className="font-semibold tabular-nums">{tl(row.amount)}</span>
          </div>
          <div className="mt-1 text-xs text-stone-500">
            {dateTR(row.date)} · {row.card}
            {row.installment && ` · ${row.installment} taksit`}
            {row.fx && ` · ${row.fx}`}
          </div>
        </div>
        {row.subscription && (
          <div className={cx("flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm",
            row.subscription === "aktif" ? "border-stone-200 dark:border-stone-700" : "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/40")}>
            <Repeat size={16} className="shrink-0 text-stone-500" />
            <span className="flex-1">
              Abonelik: <b>{{ aktif: "aktif", iptal: "iptal edildi", dusuruldu: "paket düşürüldü" }[row.subscription]}</b>
            </span>
            {row.subscription === "aktif" ? (
              <>
                <button className="btn-danger px-2 py-1 text-xs" onClick={() => subSave.mutate("iptal")}><Ban size={14} /> İptal ettim</button>
                <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setDowngrade(true)}><ArrowDownRight size={14} /> Paket değiştirdim</button>
              </>
            ) : (
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => subSave.mutate("aktif")}><RotateCcw size={14} /> Geri al</button>
            )}
          </div>
        )}
        {downgrade && <DowngradeForm sub={{ merchant: row.merchant, monthly: row.amount }} onClose={() => { setDowngrade(false); onClose(); }} />}
        <div>
          <label className="label" htmlFor="c-kind">İşlem tipi</label>
          <select id="c-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as Row["kind"])}>
            {(["harcama", "iade", "faiz_ucret", "odeme"] as const).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
          {row.kind === "faiz_ucret" && (
            <p className="mt-1 text-xs text-stone-500">Faiz değilse "Harcama" seç; özetteki faiz toplamından çıkar.</p>
          )}
        </div>
        <div>
          <label className="label" htmlFor="c-cat">Kategori</label>
          <CategoryPicker id="c-cat" value={category} onChange={setCategory} options={meta.data?.categories ?? []} />
          {categoryChanged && (
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={makeRule} onChange={(e) => setMakeRule(e.target.checked)} />
              <span>Aynı işyerinin gelecekteki işlemlerine de uygula</span>
            </label>
          )}
        </div>
        <div>
          <label className="label" htmlFor="c-desc">Açıklama</label>
          <input id="c-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="c-note">Not</label>
          <input id="c-note" className="input" value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="ör. iş gideri, hediye" />
        </div>
        {row.deleted && <p className="text-sm text-amber-600">Bu işlem silinmiş; raporlara dahil değil.</p>}
      </div>
    </Modal>
  );
}
