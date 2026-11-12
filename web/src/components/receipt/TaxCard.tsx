// Fişin vergi bilgisi (yetenek receipt_tax; mobil components/fis/VergiKarti.tsx ve KayitliVergi.tsx): KDV toplamı ve
// oran oran dökümü, satıcının vergi numarası, vergi dairesi, unvan. Fiş onayında ve kayıtlı giderde aynı kart; dokununca
// düzenlenir. Eski sunucuda (alan yok) gösterilmez.
import { hataMetni } from "../../lib/hataAcikla";
import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, type Row } from "../../api";
import { parseAmount, tl } from "../../format";
import { useUI } from "../../ui";
import { useSave } from "../hooks";
import Modal from "../Modal";

export interface VatLine { rate: number; amount: number }
export interface TaxInfo {
  vat_total?: number | null;
  vat_lines?: VatLine[] | null;
  tax_no?: string | null;
  tax_office?: string | null;
  legal_name?: string | null;
}

/** Sunucu bu alanları biliyor mu (okuma yanıtında / satırda anahtar var mı). */
export const hasTax = (x: object | null | undefined) => !!x && "vat_total" in x;
export const taxFilled = (v: TaxInfo) => !!(v.vat_total || v.vat_lines?.length || v.tax_no || v.tax_office || v.legal_name);
export const taxFields = (v: TaxInfo): TaxInfo => ({
  vat_total: v.vat_total ?? null, vat_lines: v.vat_lines ?? [], tax_no: v.tax_no ?? null, tax_office: v.tax_office ?? null,
  legal_name: v.legal_name ?? null,
});

/** VKN kontrol hanesi (sunucu app/vat.py ile aynı). */
export function vknValid(v: string): boolean {
  if (!/^\d{10}$/.test(v)) return false;
  let s = 0;
  for (let i = 0; i < 9; i++) {
    const t = (Number(v[i]) + 9 - i) % 10;
    let x = (t * 2 ** (9 - i)) % 9;
    if (t !== 0 && x === 0) x = 9;
    s += x;
  }
  return (10 - (s % 10)) % 10 === Number(v[9]);
}

const RATES = [1, 10, 20] as const;
const sum = (l: VatLine[]) => Math.round(l.reduce((a, x) => a + x.amount, 0) * 100) / 100; // parity-ok: GİRDİ
/** "%1 ₺18,25 · %20 ₺3,33" */
export const vatBreakdown = (l: VatLine[] | null | undefined) => (l ?? []).map((x) => `%${x.rate} ${tl(x.amount)}`).join(" · ");

export default function TaxCard({ value, onSave, readOnly }: {
  value: TaxInfo;
  /** düzenleme kaydı: onayda yerel durum, kayıtlı işlemde PATCH; hata fırlatırsa pencere açık kalır */
  onSave?: (v: TaxInfo) => Promise<void> | void;
  readOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const filled = taxFilled(value);
  const canEdit = !!onSave && !readOnly;
  if (!filled && !canEdit) return null;
  const vat = value.vat_total ?? (value.vat_lines?.length ? sum(value.vat_lines) : null);
  const breakdown = vatBreakdown(value.vat_lines);
  const row = (label: string, children: ReactNode) => (
    <li>
      <button type="button" disabled={!canEdit} onClick={() => setOpen(true)}
        className="flex w-full items-start gap-3 px-3 py-2.5 text-left enabled:hover:bg-stone-50 dark:enabled:hover:bg-stone-800/50">
        <span className="w-20 shrink-0 text-stone-500">{label}</span>
        <span className="flex min-w-0 flex-1 flex-col items-end gap-0.5 text-right break-words">{children}</span>
        {canEdit && <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />}
      </button>
    </li>
  );
  return (
    <section aria-label="Vergi bilgisi" className="space-y-1">
      <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">Vergi bilgisi</h3>
      <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
        {!filled ? row("KDV", <span className="text-stone-400">Fişte okunamadı · ekle</span>) : (
          <>
            {row("KDV", <>
              <span className="font-semibold tabular-nums">{vat != null ? tl(vat) : "—"}</span>
              {breakdown && <span className="text-xs tabular-nums text-stone-500">{breakdown}</span>}
            </>)}
            {(value.tax_no || value.tax_office) && row("Vergi no", <>
              <span className="tabular-nums">{value.tax_no ?? "—"}</span>
              {value.tax_office && <span className="text-xs text-stone-500">{value.tax_office} V.D.</span>}
            </>)}
            {value.legal_name && row("Unvan", <span>{value.legal_name}</span>)}
          </>
        )}
      </ul>
      {open && <TaxEdit value={value} onClose={() => setOpen(false)} onSave={async (v) => { await onSave!(v); setOpen(false); }} />}
    </section>
  );
}

const money = (n: number | null | undefined) => (n != null ? String(n).replace(".", ",") : "");
const parsed = (s: string) => { const n = parseAmount(s); return Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : null; };

function TaxEdit({ value, onClose, onSave }: { value: TaxInfo; onClose: () => void; onSave: (v: TaxInfo) => Promise<void> }) {
  const byRate = new Map((value.vat_lines ?? []).map((x) => [x.rate, x.amount]));
  const others = (value.vat_lines ?? []).filter((x) => !(RATES as readonly number[]).includes(x.rate));
  const [rates, setRates] = useState<Record<number, string>>(() => Object.fromEntries(RATES.map((r) => [r, money(byRate.get(r))])));
  const [total, setTotal] = useState(money(value.vat_total));
  const [taxNo, setTaxNo] = useState(value.tax_no ?? "");
  const [office, setOffice] = useState(value.tax_office ?? "");
  const [legal, setLegal] = useState(value.legal_name ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lines: VatLine[] = [...RATES.flatMap((r) => { const a = parsed(rates[r]); return a ? [{ rate: r, amount: a }] : []; }), ...others]
    .sort((a, b) => a.rate - b.rate);
  const linesSum = lines.length ? sum(lines) : null;
  const tot = parsed(total);
  const no = taxNo.replace(/\D/g, "");
  const noErr = !no ? null : no.length === 11 ? "11 haneli T.C. kimlik numarası saklanmaz; firmanın 10 haneli vergi numarasını yaz."
    : no.length !== 10 ? "Vergi numarası 10 hanedir." : !vknValid(no) ? "Bu vergi numarası geçerli değil; fişteki numarayı kontrol et." : null;

  async function save() {
    if (noErr) { setErr(noErr); return; }
    setBusy(true);
    setErr(null);
    try {
      await onSave({ vat_total: tot ?? linesSum, vat_lines: lines, tax_no: no || null, tax_office: office.trim() || null, legal_name: legal.trim() || null });
    } catch (e) {
      setErr(hataMetni(e, "Kaydedilemedi"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Vergi bilgisi" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={busy} onClick={() => void save()}>{busy ? "Kaydediliyor…" : "Kaydet"}</button>
    </>}>
      <div className="space-y-3 text-sm">
        <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">KDV</h3>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {RATES.map((r) => (
            <div key={r}>
              <label className="label" htmlFor={`vat-${r}`}>%{r}</label>
              <input id={`vat-${r}`} className="input text-right" inputMode="decimal" placeholder="0,00" aria-label={`KDV yüzde ${r}`}
                value={rates[r]} onChange={(e) => setRates((s) => ({ ...s, [r]: e.target.value }))} />
            </div>
          ))}
          <div>
            <label className="label" htmlFor="vat-total">Toplam KDV</label>
            <input id="vat-total" className="input text-right" inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)}
              placeholder={linesSum != null ? money(linesSum) : "0,00"} />
          </div>
        </div>
        {linesSum != null && tot != null && Math.abs(linesSum - tot) > 0.05 && (
          <p role="status" className="text-xs text-amber-700 dark:text-amber-400">
            Oranların toplamı {tl(linesSum)}; toplam KDV {tl(tot)}. Fişteki TOPKDV'yi kontrol et.</p>
        )}
        <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">Satıcı</h3>
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="tax-no">Vergi no</label>
            <input id="tax-no" className="input tabular-nums" inputMode="numeric" autoComplete="off" maxLength={14} placeholder="10 hane"
              value={taxNo} onChange={(e) => setTaxNo(e.target.value.replace(/[^\d ]/g, ""))} />
          </div>
          <div>
            <label className="label" htmlFor="tax-office">Vergi dairesi</label>
            <input id="tax-office" className="input" autoComplete="off" maxLength={60} placeholder="ör. Kadıköy" value={office} onChange={(e) => setOffice(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="tax-legal">Unvan</label>
          <textarea id="tax-legal" className="input resize-none" rows={2} autoComplete="off" maxLength={120} placeholder="Şirketin resmi adı"
            value={legal} onChange={(e) => setLegal(e.target.value.replace(/\n/g, " "))} />
        </div>
        {noErr && no.length >= 10 && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{noErr}</p>}
        {err && !(err === noErr && no.length >= 10) && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{err}</p>}
      </div>
    </Modal>
  );
}

/** Kayıtlı giderin vergi bilgisi: satırdaki alanlar; dokununca PATCH /api/entries/{id}. Fişsiz, boş kayıtta görünmez. */
export function SavedTax({ row, readOnly }: { row: Row; readOnly?: boolean }) {
  const ui = useUI();
  const [v, setV] = useState<TaxInfo>(() => taxFields(row));
  const patch = useSave((next: TaxInfo) => api.patchEntry(row.id, {
    vat_total: next.vat_total ?? null, vat_lines: next.vat_lines ?? [], tax_no: next.tax_no ?? "",
    tax_office: next.tax_office ?? "", legal_name: next.legal_name ?? "",
  }), (_r, next) => { setV(next); ui.toast("Vergi bilgisi kaydedildi"); }, { inline: true });
  if (!hasTax(row)) return null;
  // fiş fotoğrafı ya da okunmuş vergi bilgisi olan gider: elle girilen fişsiz kayıtta kart kalabalık yapmasın
  if (!taxFilled(v) && !row.receipt_file) return null;
  return <TaxCard value={v} readOnly={readOnly} onSave={readOnly ? undefined : (next) => patch.mutateAsync(next).then(() => undefined)} />;
}
