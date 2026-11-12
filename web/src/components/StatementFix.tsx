import { hataMetni } from "../lib/hataAcikla";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, EyeOff, FileText, ListPlus, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type FixCheck, type Statement, type StatementRow } from "../api";
import { cx, dateTR, maskText, tl } from "../format";
import { DUZELT, grupla, onizleme, sonucMetni, TURLER, turAnahtari, tutarOku, tutarYaz, type GrupKey } from "../lib/ekstreDuzelt";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";
import SorguDurumu from "./SorguDurumu";

/** Yazmayı bırakınca (350 ms) değer: önizleme her tuşta sunucuya gitmesin. */
function useBekle<T>(v: T, ms = 350): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

/** "Bu değişiklikle: Tuttu ✓ / Kalan fark ₺X" — sunucunun dry_run sonucu (kayıttaki hesapla aynı). */
function Onizleme({ check, loading }: { check: FixCheck | null | undefined; loading: boolean }) {
  const raw = loading ? DUZELT.hesaplaniyor : onizleme(check);
  const text = raw && maskText(raw);
  if (!text) return null;
  const ok = !loading && check?.ok !== false;
  return (
    <p role="status" data-testid="duzelt-onizleme"
      className={cx("rounded-lg p-2.5 text-sm font-medium", ok ? "bg-green-50 text-green-800 dark:bg-green-950/50 dark:text-green-300"
        : "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200")}>
      {ok && <CheckCircle2 size={14} aria-hidden className="mr-1 inline" />}{text}
    </p>
  );
}

function TurSecici({ value, onChange }: { value: string; onChange: (k: string) => void }) {
  return (
    <div role="radiogroup" aria-label={DUZELT.tur} className="flex flex-wrap gap-1.5">
      {TURLER.map((t) => (
        <button key={t.key} type="button" role="radio" aria-checked={value === t.key} onClick={() => onChange(t.key)}
          className={cx("rounded-full border px-3 py-1 text-sm", value === t.key
            ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 text-stone-700 dark:border-stone-700 dark:text-stone-200")}>
          {t.label}</button>
      ))}
    </div>
  );
}

const bildir = (ui: ReturnType<typeof useUI>, metin: string, undoId?: string | null) =>
  ui.toast(metin, undoId ? { undo: () => api.undoImport(undoId, true) } : undefined);

/** Eksik satır ekle: tutar farktan, tür öneriden, tarih kesim tarihi; canlı "Tuttu ✓". */
export function AddRowSheet({ s, onClose }: { s: Statement; onClose: () => void }) {
  const ui = useUI();
  const sug = s.fix_hint?.suggest;
  const [tur, setTur] = useState(sug ? turAnahtari(sug.txn_type) : "harcama");
  const [tutar, setTutar] = useState(tutarYaz(sug?.amount ?? (s.fix_diff != null ? Math.abs(s.fix_diff) : null)));
  const [tarih, setTarih] = useState(sug?.txn_date ?? s.period_end.slice(0, 10));
  const def = TURLER.find((t) => t.key === tur)!;
  const [aciklama, setAciklama] = useState("");
  const amount = tutarOku(tutar);
  const body = { txn_type: def.type, amount: amount ?? 0, txn_date: tarih, description: aciklama.trim() || def.desc };
  const dry = useBekle(body);
  const q = useQuery({
    queryKey: ["stmt-fix-add", s.id, dry], enabled: dry.amount > 0, staleTime: 0, retry: false,
    queryFn: () => api.statementRowAdd(s.id, { ...dry, dry_run: true }),
  });
  const save = useSave(() => api.statementRowAdd(s.id, body), (r) => {
    onClose();
    bildir(ui, sonucMetni("eklendi", r.check), r.undo_id);
  }, { inline: true });
  const loading = amount != null && (q.isFetching || JSON.stringify(dry) !== JSON.stringify(body));
  return (
    <Modal title={DUZELT.satirEkle} onClose={onClose} dirty={false} onSave={() => amount && save.mutate(undefined)} footer={<>
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary ml-auto" disabled={!amount || save.isPending} onClick={() => save.mutate(undefined)} data-testid="duzelt-ekle">
        {DUZELT.ekle}</button>
    </>}>
      <div className="space-y-3 pb-1 text-sm">
        {s.fix_hint && <p className="rounded-lg bg-stone-50 p-2.5 text-stone-700 dark:bg-stone-800/60 dark:text-stone-200">
          <b>{DUZELT.oneri}:</b> {maskText(s.fix_hint.text)}</p>}
        <div className="space-y-1"><div className="text-xs font-medium text-stone-500 dark:text-stone-400">{DUZELT.tur}</div>
          <TurSecici value={tur} onChange={setTur} /></div>
        <label className="block space-y-1"><span className="text-xs font-medium text-stone-500 dark:text-stone-400">{DUZELT.tutar}</span>
          <input className="input w-full tabular-nums" inputMode="decimal" value={tutar} onChange={(e) => setTutar(e.target.value)}
            aria-describedby="duzelt-isaret" data-testid="duzelt-tutar" /></label>
        <p id="duzelt-isaret" className="text-xs text-stone-500 dark:text-stone-400">{DUZELT.isaretNotu}</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block space-y-1"><span className="text-xs font-medium text-stone-500 dark:text-stone-400">{DUZELT.tarih}</span>
            <input className="input w-full" type="date" value={tarih} onChange={(e) => setTarih(e.target.value)} /></label>
          <label className="block space-y-1"><span className="text-xs font-medium text-stone-500 dark:text-stone-400">{DUZELT.aciklama}</span>
            <input className="input w-full" value={aciklama} placeholder={def.desc} maxLength={120} onChange={(e) => setAciklama(e.target.value)} /></label>
        </div>
        <Onizleme check={q.data?.check} loading={loading} />
        {(save.error || q.error) && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{hataMetni(save.error || q.error)}</p>}
      </div>
    </Modal>
  );
}

/** Tek satırın düzenleyicisi: tutar, tür, hesaba katma; canlı önizleme. */
function RowEditor({ sid, row, onDone }: { sid: number; row: StatementRow; onDone: () => void }) {
  const ui = useUI();
  const [tur, setTur] = useState(turAnahtari(row.txn_type, row.description));
  const [tutar, setTutar] = useState(tutarYaz(row.amount));
  const [katma, setKatma] = useState(!!row.is_deleted);
  const def = TURLER.find((t) => t.key === tur)!;
  const amount = tutarOku(tutar);
  const body = { amount: amount ?? Math.abs(row.amount), txn_type: def.type, is_deleted: katma };
  const dry = useBekle(body);
  const q = useQuery({
    queryKey: ["stmt-fix-edit", sid, row.id, dry], staleTime: 0, retry: false,
    queryFn: () => api.statementRowEdit(sid, row.id, { ...dry, dry_run: true }),
  });
  const save = useSave(() => api.statementRowEdit(sid, row.id, body), (r) => {
    onDone();
    bildir(ui, sonucMetni("duzeltildi", r.check), r.undo_id);
  }, { inline: true });
  const changed = !!amount && (Math.abs(amount - Math.abs(row.amount)) >= 0.005 || def.type !== row.txn_type || katma !== !!row.is_deleted);
  const loading = q.isFetching || JSON.stringify(dry) !== JSON.stringify(body);
  return (
    <div className="space-y-2.5 border-t border-stone-100 pt-2.5 dark:border-stone-800" data-testid="duzelt-satir-editor">
      <TurSecici value={tur} onChange={setTur} />
      <div className="flex flex-wrap items-center gap-2">
        <input className="input w-36 tabular-nums" inputMode="decimal" aria-label={DUZELT.tutar} value={tutar} onChange={(e) => setTutar(e.target.value)} />
        <button type="button" className="btn-ghost px-2 text-sm" aria-pressed={katma} onClick={() => setKatma(!katma)}>
          {katma ? <><RotateCcw size={14} aria-hidden /> {DUZELT.hesabaKat}</> : <><EyeOff size={14} aria-hidden /> {DUZELT.hesabaKatma}</>}</button>
      </div>
      <p className="text-xs text-stone-500 dark:text-stone-400">{DUZELT.isaretNotu}</p>
      {changed && <Onizleme check={q.data?.check} loading={loading} />}
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
        <button className="btn-primary" disabled={!changed || save.isPending} onClick={() => save.mutate(undefined)}>{DUZELT.kaydet}</button>
      </div>
      {save.error && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{hataMetni(save.error)}</p>}
    </div>
  );
}

/** Satırları düzenle: gruplu satırlar (Harcamalar, Taksitler, Ek kart, İadeler, Ödemeler, Faiz ve ücretler); dokun →
 *  tutar / tür / hesaba katma; üstte canlı durum ("Tuttu ✓" ya da fark ve öneri), altta "Eksik satır ekle".
 *  odak: denklem kutusundaki rakama dokunulduysa yalnız o gruplar. Öneri bir satırı gösteriyorsa o açık gelir. */
export function EditRowsSheet({ s, onClose, odak }: { s: Statement; onClose: () => void; odak?: GrupKey[] | null }) {
  const q = useQuery({ queryKey: ["stmt-rows", s.id], queryFn: () => api.statementRows(s.id), staleTime: 0 });
  const [open, setOpen] = useState<number | null>(s.fix_hint?.row_id ?? null);
  const [hepsi, setHepsi] = useState(!odak);
  const [ekle, setEkle] = useState(false);
  const check = q.data?.check;
  const gruplar = q.data ? grupla(q.data.rows, s.card_no, hepsi ? null : odak) : [];
  return (<>
    <Modal title={DUZELT.satirlar} onClose={onClose} hidden={ekle} footer={s.can_fix ? <>
      <a className="btn-ghost" href={`/api/statements/${s.id}/file`} target="_blank" rel="noreferrer"><FileText size={15} aria-hidden /> {DUZELT.pdf}</a>
      <button className="btn-primary ml-auto" onClick={() => setEkle(true)} data-testid="duzelt-satirlar-ekle"><ListPlus size={15} aria-hidden /> {DUZELT.satirEkle}</button>
    </> : undefined}>
      <SorguDurumu q={q} kompakt />
      {q.data && (
        <div className="space-y-3 pb-1 text-sm">
          {check?.ok === false && (check.title || check.hint) && (
            <p role="status" className="rounded-lg bg-amber-50 p-2.5 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" data-testid="duzelt-durum">
              {check.title && <b className="block">{maskText(check.title)}</b>}{check.hint ? maskText(check.hint.text) : null}</p>
          )}
          {check?.ok !== false && <p role="status" className="rounded-lg bg-green-50 p-2.5 font-medium text-green-800 dark:bg-green-950/50 dark:text-green-300" data-testid="duzelt-durum">{DUZELT.tuttu}</p>}
          <p className="text-xs text-stone-500 dark:text-stone-400">{DUZELT.satirDokun} {DUZELT.pdfYan}</p>
          {q.data.rows.length === 0 && <p className="text-stone-500">{DUZELT.bulunamadi}</p>}
          {gruplar.map((g) => (
            <section key={g.key} aria-label={g.label} data-testid={`duzelt-grup-${g.key}`}>
              <h3 className="mb-1 flex justify-between gap-2 px-1 text-xs font-semibold text-stone-500 uppercase dark:text-stone-400">
                <span>{g.label}</span><span className="tabular-nums">{g.rows.length}</span></h3>
              <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
                {g.rows.map((r) => (
                  <li key={r.id} className={cx("px-3 py-2", s.fix_hint?.row_id === r.id && "bg-amber-50/60 dark:bg-amber-950/30")}>
                    <button className="flex w-full items-start gap-2 text-left" aria-expanded={open === r.id} disabled={!s.can_fix}
                      onClick={() => setOpen(open === r.id ? null : r.id)}>
                      <span className="min-w-0 flex-1">
                        <span className={cx("block break-words", !!r.is_deleted && "text-stone-400 line-through")}>{maskText(r.description)}</span>
                        <span className="block text-xs text-stone-500 dark:text-stone-400">{dateTR(r.txn_date)} · {TURLER.find((t) => t.key === turAnahtari(r.txn_type, r.description))?.label}
                          {r.installment_no ? ` · ${r.installment_no}. taksit` : ""}{r.is_deleted ? ` · ${DUZELT.katilmiyor}` : ""}</span>
                      </span>
                      <span className={cx("shrink-0 tabular-nums", r.amount < 0 && "text-green-700 dark:text-green-400")}>{tl(r.amount)}</span>
                    </button>
                    {open === r.id && <RowEditor sid={s.id} row={r} onDone={() => { setOpen(null); void q.refetch(); }} />}
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {!hepsi && <button className="btn-ghost w-full justify-center text-sm" onClick={() => setHepsi(true)}>{DUZELT.tumSatirlar}</button>}
        </div>
      )}
    </Modal>
    {ekle && <AddRowSheet s={s} onClose={() => { setEkle(false); void q.refetch(); }} />}
  </>);
}
