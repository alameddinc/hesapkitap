import { useQuery } from "@tanstack/react-query";
import { Link2, Link2Off, Repeat, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type Payment, type Recurring, type Row } from "../api";
import { dateTR, parseAmount, tl } from "../format";
import type { EntryDraft } from "../ui";
import CategoryPicker from "./CategoryPicker";
import { useMeta, useSave } from "./hooks";
import PayrollPreview from "./PayrollPreview";
import SalaryChanges from "./SalaryChanges";
import Modal from "./Modal";
import Segmented from "./Segmented";

const today = () => new Date().toISOString().slice(0, 10);

/** Elle gelir/gider girişi. recurring=true ise "her ay" sabit kalem olarak kaydeder. */
export default function EntryForm({ row, draft, recurring: rec, onClose, onSaved }: {
  row?: Row;
  draft?: EntryDraft;
  recurring?: Recurring;
  onClose: () => void;
  onSaved?: (msg: string) => void;
}) {
  const meta = useMeta();
  const editingEntry = row?.source === "manual" ? row : undefined;
  const [kind, setKind] = useState<"gelir" | "gider">(rec?.kind ?? (row?.kind as "gelir" | "gider") ?? draft?.kind ?? "gider");
  const [description, setDescription] = useState(rec?.description ?? row?.description ?? draft?.description ?? "");
  const [amount, setAmount] = useState(String(rec?.amount ?? row?.amount ?? draft?.amount ?? ""));
  const [date, setDate] = useState(row?.date?.slice(0, 10) ?? draft?.entry_date ?? today());
  const [category, setCategory] = useState(rec?.category ?? row?.category ?? draft?.category ?? "");
  const [note, setNote] = useState(rec?.note ?? row?.note ?? draft?.note ?? "");
  const [isRecurring, setRecurring] = useState(!!rec || !!draft?.recurring);
  const [day, setDay] = useState(String(rec?.day_of_month ?? new Date().getDate()));
  const [endMonth, setEndMonth] = useState(rec?.end_month ?? "");
  const [payment, setPayment] = useState<Payment>(row?.payment ?? draft?.payment ?? "nakit");
  const [isGross, setIsGross] = useState(!!rec?.gross_amount);
  const [gross, setGross] = useState(rec?.gross_amount ? String(rec.gross_amount) : "");
  const [person, setPerson] = useState(row?.counterparty ?? "");
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const [err, setErr] = useState<string | null>(null);
  const showLink = !!editingEntry && kind === "gider" && payment === "kredi_karti" && editingEntry.payment === "kredi_karti";
  const cands = useQuery({
    queryKey: ["cands", editingEntry?.id],
    queryFn: () => api.linkCandidates(editingEntry!.id),
    enabled: showLink && !editingEntry?.linked,
  });

  const done = (msg: string) => {
    onSaved?.(msg);
    onClose();
  };
  const save = useSave(async () => {
    const amt = parseAmount(amount);
    if (!description.trim() || ((!isGross || kind !== "gelir") && (!(amt >= 0) || amount === ""))) throw new Error("Açıklama ve tutar gerekli");
    const g = parseAmount(gross);
    const grossMode = isGross && kind === "gelir" && (isRecurring || !!rec);
    if (grossMode && !(g > 0)) throw new Error("Brüt tutar gerekli");
    const common = { description: description.trim(), amount: grossMode ? 0 : amt, kind, category: category || null, note: note || null,
      ...(isRecurring || rec ? { gross_amount: grossMode ? g : null } : {}) };
    const pay = kind === "gider" && !isRecurring && !rec ? { payment } : {};
    const who = !isRecurring && !rec ? { counterparty: person.trim() || null } : {};
    if (rec) return api.patchRecurring(rec.id, { ...common, day_of_month: Number(day), end_month: endMonth || null });
    if (isRecurring)
      return api.createRecurring({ ...common, day_of_month: Number(day), start_month: date.slice(0, 7), end_month: endMonth || null });
    if (editingEntry) return api.patchEntry(editingEntry.id, { ...common, ...pay, ...who, entry_date: date });
    return api.createEntry({ ...common, ...pay, ...who, entry_date: date, legal_name: draft?.legal_name ?? null });
  }, (r) => done(isRecurring || rec ? "Sabit kalem kaydedildi"
    : r && typeof r === "object" && "debt_settled" in r && r.debt_settled ? `Kaydedildi · ${person} ile borç/alacak kapandı`
    : r && typeof r === "object" && "linked" in r && r.linked ? "Kaydedildi · ekstredeki işlemle eşleşti" : "Kaydedildi"));
  const link = useSave((txnId: number | null) => api.linkEntry(editingEntry!.id, txnId),
    () => done("Eşleşme güncellendi"));

  const del = useSave(async () => {
    if (rec) return api.deleteRecurring(rec.id);
    if (editingEntry) return api.deleteEntry(editingEntry.id);
  }, () => done("Silindi"));

  const cats = kind === "gelir" ? meta.data?.income_categories ?? [] : meta.data?.categories ?? [];
  const title = rec ? "Sabit kalemi düzenle" : editingEntry ? "Kaydı düzenle" : kind === "gelir" ? "Gelir ekle" : "Gider ekle";

  return (
    <Modal title={title} onClose={onClose} footer={
      <>
        {(editingEntry || rec) && (
          <button className="btn-danger" onClick={() => confirm("Silinsin mi?") && del.mutate(undefined)}>
            <Trash2 size={16} /> Sil
          </button>
        )}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending}
          onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>
          Kaydet
        </button>
      </>
    }>
      <div className="space-y-3">
        <Segmented value={kind} onChange={setKind}
          options={[{ value: "gider", label: "Gider", tone: "red" }, { value: "gelir", label: "Gelir", tone: "green" }]} />
        <div>
          <label className="label" htmlFor="e-desc">Açıklama</label>
          <input id="e-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder={kind === "gelir" ? "ör. Maaş" : "ör. Psikolog, Kira"} autoFocus={!row && !rec} />
        </div>
        {draft?.legal_name && !editingEntry && (
          <p className="-mt-1 text-xs text-stone-500">Ekstrede muhtemelen <b>{draft.legal_name}</b> adıyla geçer; eşleştirilip markayla gruplanır.</p>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div className={isGross && kind === "gelir" && isRecurring ? "hidden" : undefined}>
            <label className="label" htmlFor="e-amt">Tutar (₺)</label>
            <input id="e-amt" className="input text-right" inputMode="decimal" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="0,00" />
          </div>
          {!isRecurring ? (
            <div>
              <label className="label" htmlFor="e-date">Tarih</label>
              <input id="e-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
          ) : (
            <div>
              <label className="label" htmlFor="e-day">Ayın kaçı</label>
              <input id="e-day" type="number" min={1} max={31} className="input" value={day} onChange={(e) => setDay(e.target.value)} />
            </div>
          )}
        </div>
        <div>
          <label className="label" htmlFor="e-cat">Kategori</label>
          <CategoryPicker id="e-cat" value={category} onChange={setCategory} options={cats} placeholder="Seç veya yaz" />
        </div>
        {!isRecurring && !rec && (
          <div>
            <label className="label" htmlFor="e-person">Kişi (opsiyonel)</label>
            <CategoryPicker id="e-person" value={person} onChange={setPerson} options={people.data ?? []}
              placeholder="Para bir kişiye gittiyse / kişiden geldiyse" />
            <p className="mt-1 text-xs text-stone-500">Transferler'de o kişinin hesabına düşer; açık borç/alacakla aynı tutarsa kapatır.</p>
          </div>
        )}
        {kind === "gider" && !isRecurring && !rec && (
          <div>
            <span className="label">Ödeme şekli</span>
            <Segmented value={payment} onChange={setPayment} options={[
              { value: "kredi_karti", label: "Kredi kartı" },
              { value: "nakit", label: "Nakit / Banka k." },
              { value: "havale", label: "Havale" },
            ]} />
            {payment === "kredi_karti" && !editingEntry?.linked && (
              <p className="mt-1 text-xs text-stone-500">Ekstre gelince aynı tutarlı kart işlemiyle otomatik eşleşir; iki kez sayılmaz.</p>
            )}
          </div>
        )}
        {showLink && editingEntry?.linked && (
          <div className="flex items-center gap-2 rounded-lg bg-green-50 p-3 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            <Link2 size={16} className="shrink-0" />
            <span className="flex-1">Ekstredeki kart işlemiyle eşleşti; gider ekstreden sayılıyor.</span>
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => link.mutate(null)}><Link2Off size={14} /> Kaldır</button>
          </div>
        )}
        {showLink && !editingEntry?.linked && (cands.data?.length ?? 0) > 0 && (
          <div className="rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
            <div className="mb-2 font-medium">Ekstrede olası karşılıkları</div>
            <ul className="space-y-1">
              {cands.data!.map((c) => (
                <li key={c.id} className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate">
                    {c.description} <span className="text-xs text-stone-500">· {dateTR(c.txn_date)} · {c.account}</span>
                  </span>
                  <span className="tabular-nums">{tl(c.amount)}</span>
                  <button className="btn-primary px-2 py-1 text-xs" onClick={() => link.mutate(c.id)}>Eşleştir</button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!editingEntry && (
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
            <input type="checkbox" checked={isRecurring} disabled={!!rec} onChange={(e) => setRecurring(e.target.checked)} />
            <Repeat size={16} className="text-stone-500" />
            <span>Her ay tekrarlansın <span className="text-stone-500">(maaş, kira, aidat…)</span></span>
          </label>
        )}
        {isRecurring && kind === "gelir" && (
          <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" checked={isGross} onChange={(e) => setIsGross(e.target.checked)} />
              <span><b>Brüt maaş</b> — net her ay vergi dilimine göre otomatik hesaplansın</span>
            </label>
            {isGross && (
              <>
                <input className="input text-right" inputMode="decimal" placeholder="Aylık brüt (₺)" value={gross}
                  onChange={(e) => setGross(e.target.value)} />
                <PayrollPreview gross={parseAmount(gross)} year={Number((rec?.start_month ?? date).slice(0, 4))} />
              </>
            )}
          </div>
        )}
        {rec && rec.kind === "gelir" && !!rec.gross_amount && <SalaryChanges rid={rec.id} />}
        {isRecurring && !isGross && (
          <p className="rounded-lg bg-stone-100 p-2 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-400">
            Tutar her ay değişiyorsa (ör. brüt maaşta net) burada ortalama/beklenen tutarı yaz. O ay yatınca "Gerçekleşti"de
            gerçek tutarı girersin; ileriki aylar için Planlama'da ay ay tutar verebilirsin.
          </p>
        )}
        {isRecurring && (
          <div className="grid grid-cols-2 gap-3">
            {!rec && (
              <div>
                <label className="label" htmlFor="e-start">Başlangıç ayı</label>
                <input id="e-start" type="month" className="input" value={date.slice(0, 7)}
                  onChange={(e) => setDate(`${e.target.value}-01`)} />
              </div>
            )}
            <div>
              <label className="label" htmlFor="e-end">Bitiş ayı (opsiyonel)</label>
              <input id="e-end" type="month" className="input" value={endMonth} onChange={(e) => setEndMonth(e.target.value)} />
            </div>
          </div>
        )}
        <div>
          <label className="label" htmlFor="e-note">Not</label>
          <input id="e-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
      </div>
    </Modal>
  );
}
