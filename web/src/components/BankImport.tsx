import { AlertTriangle, Check, Landmark, Loader2, Plus, X } from "lucide-react";
import { TERIM } from "../lib/terimler";
import { errorText } from "../lib/apiError";
import { useEffect, useRef, useState } from "react";
import { cx, dateTR, maskText, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import { isQuotaFull, QuotaFullError, QuotaFullModal, useDocQuota, type QuotaFull } from "./DocQuota";

type Key = "date" | "description" | "amount" | "debit" | "credit" | "balance";
interface Preview {
  header_row: number;
  columns: string[];
  mapping: Record<Key, number | null>;
  profile_saved: boolean;
  total: number;
  counts: Record<string, number>;
  sample: { date: string; description: string; amount: number; type: string; who?: string | null }[];
  pdf?: boolean;
  verified?: boolean;
  bank?: string;
  chain_bad?: number[];
  warning?: string | null; // ör. kart dökümü olabilir (harcamalar gelir görünür)
  balance_change?: { account: string; current: number; new: number; date: string } | null;
  checks?: string[]; // şüpheli değişiklik: içe aktarmadan önce onay istenir
  iban?: string | null;
  account_match?: AccountMatch | { status?: undefined }; // döküm hangi hesaba gidecek (app/sync.py resolve_account)
  /** aynı hesabın aynı hareketi başka belgeden (PDF ↔ Excel/CSV) zaten yüklü: eklenmez (counts.cakisma) */
  overlap?: { count: number; from: string; to: string; message: string } | null;
}

interface AccountCand { id: number; name: string; balance: number | null; as_of: string | null; iban_last4: string | null }
type AccountMatch =
  | { status: "exact"; account_id: number; name: string; via: "iban" | "alias" | "name"; iban_last4: string | null }
  | { status: "ask"; bank: string; default_id: number | null; candidates: AccountCand[]; iban_last4: string | null }
  | { status: "new"; name: string; iban_last4: string | null }
  | { status: "none"; candidates: AccountCand[]; iban_last4: string | null };
/** Hesap seçimi: null = sunucu karar versin (ask ise Yapılacaklar'da sorulur), sayı = o hesap, "new" = yeni hesap aç. */
type AccountChoice = number | "new" | null;

const VIA: Record<string, string> = { iban: "IBAN'dan tanındı", alias: "daha önce bu hesaba eklemiştin", name: "banka adı tutuyor" };

/** "Bu döküm 'Garanti' hesabına mı ait?" — önizlemede sorulur, cevap içe aktarmayla gider. */
function AccountQuestion({ m, value, onChange }: { m: AccountMatch; value: AccountChoice; onChange: (v: AccountChoice) => void }) {
  const iban = m.iban_last4 ? ` · IBAN ••${m.iban_last4}` : "";
  if (m.status === "exact") return (
    <p className="flex items-center gap-2 text-sm text-stone-700 dark:text-stone-300">
      <Landmark size={15} className="shrink-0 text-stone-400" aria-hidden="true" />
      <span><b>{m.name}</b> hesabına eklenecek <span className="text-xs text-stone-500">({VIA[m.via] ?? m.via}{iban})</span></span>
    </p>
  );
  if (m.status === "new") return (
    <p className="flex items-center gap-2 text-sm text-stone-700 dark:text-stone-300">
      <Plus size={15} className="shrink-0 text-stone-400" aria-hidden="true" />
      <span>Yeni hesap açılacak: <b>{m.name}</b><span className="text-xs text-stone-500">{iban}</span></span>
    </p>
  );
  if (m.status === "none") return m.candidates.length ? (
    <label className="block text-sm">
      <span className="label">Hangi hesaba? (isteğe bağlı)</span>
      <select className="input" value={typeof value === "number" ? value : ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Hesap belirtme</option>
        {m.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}{c.iban_last4 ? ` ••${c.iban_last4}` : ""}{c.balance != null ? ` · ${tl(c.balance)}` : ""}</option>)}
      </select>
    </label>
  ) : null;
  const cands = m.candidates.filter((c) => c.id !== m.default_id);
  const single = cands.length === 1 ? cands[0] : null;
  const opt = (checked: boolean) => cx("flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm transition-colors",
    checked ? "border-blue-500 bg-blue-50 ring-2 ring-blue-500/20 dark:border-blue-500 dark:bg-blue-950/40"
      : "border-stone-200 bg-white hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-900 dark:hover:bg-stone-800");
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-3 dark:border-blue-900/60 dark:bg-blue-950/20">
    <fieldset className="space-y-2">
      <legend className="font-display text-[15px] font-semibold tracking-tight">
        {single ? `Bu döküm “${single.name}” hesabına mı ait?` : `Bu döküm hangi ${m.bank} hesabına ait?`}</legend>
      <p className="text-xs text-stone-500 dark:text-stone-400">
        {m.bank} için elle açtığın {cands.length > 1 ? `${cands.length} hesap` : "bir hesap"} var{iban}. Aynı hesapsa hareketler oraya eklenir,
        elle girdiklerinle eşleşir; sonraki dökümler sormadan gider.</p>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {cands.map((c) => (
          <label key={c.id} className={opt(value === c.id)}>
            <input type="radio" name="bank-acc" className="accent-blue-600" checked={value === c.id} onChange={() => onChange(c.id)} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{single ? "Evet, " : ""}{c.name}{c.iban_last4 ? ` ••${c.iban_last4}` : ""}</span>
              {c.balance != null && <span className="block text-xs text-stone-500 tabular-nums">{tl(c.balance)}{c.as_of ? ` · ${dateTR(c.as_of)}` : ""}</span>}
            </span>
            {value === c.id && <Check size={15} className="text-blue-600" aria-hidden="true" />}
          </label>
        ))}
        <label className={opt(value === "new")}>
          <input type="radio" name="bank-acc" className="accent-blue-600" checked={value === "new"} onChange={() => onChange("new")} />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">Yeni hesap aç</span>
            <span className="block text-xs text-stone-500">{m.default_id ? `“${m.candidates.find((c) => c.id === m.default_id)?.name ?? m.bank}” ayrı hesap olarak kalır` : `ayrı bir “${m.bank}” hesabı açılır`}</span>
          </span>
          {value === "new" && <Check size={15} className="text-blue-600" aria-hidden="true" />}
        </label>
      </div>
      {value === null && <p className="text-xs text-stone-500">Seçmezsen yine içe aktarılır; hangi hesap olduğunu Yapılacaklar'da sorarız.</p>}
    </fieldset>
    </div>
  );
}

const LABEL: Record<string, string> = {
  gelir: "Gelir", gider: "Gider", kart_odemesi: "Kart ödemesi", virman: TERIM.kendiHesaplar.ad, tekrar: "Zaten var",
  cakisma: "Başka belgeden yüklü",
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
  if (isQuotaFull(j)) throw new QuotaFullError(j); // 422: belge okuma hakkı doldu (hazır sürüm)
  if (!r.ok) throw new Error(errorText(j, r.status));
  return j as T;
}

/** Vadesiz hesap dökümü (Excel/CSV) içe aktarma: sütun tahmini → önizleme → içe aktar. */
/** incoming: Ekstreler'deki tek yükleme alanından gelen hesap dökümü (önizleme hemen başlar). */
export default function BankImport({ incoming, onDone }: { incoming?: { file: File; password?: string } | null; onDone?: () => void } = {}) {
  const ui = useUI();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (incoming && incoming.file !== file) {
      setFile(incoming.file);
      setPassword(incoming.password);
      setPv(null);
      void preview(incoming.file, undefined, undefined, incoming.password);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming]);
  const [pv, setPv] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sure, setSure] = useState(false); // şüpheli değişiklik onayı
  const [acc, setAcc] = useState<AccountChoice>(null); // "Bu döküm X hesabına mı ait?" cevabı
  const quota = useDocQuota();
  const [full, setFull] = useState<Partial<QuotaFull> | null>(null); // belge okuma hakkı doldu uyarısı
  const onFail = (e: unknown) => {
    if (e instanceof QuotaFullError) setFull(e.info);
    setErr((e as Error).message);
  };

  const preview = async (f: File, mapping?: Preview["mapping"], headerRow?: number, pw?: string) => {
    setBusy(true);
    setErr(null);
    try {
      setSure(false);
      setAcc(null);
      setPv(await postForm<Preview>("/api/bank-import/preview", f, {
        ...(mapping ? { mapping: JSON.stringify(mapping), header_row: String(headerRow ?? 0) } : {}),
        ...(pw ?? password ? { password: (pw ?? password)! } : {}),
      }));
    } catch (e) {
      onFail(e);
    } finally {
      setBusy(false);
      void quota.refresh();
    }
  };
  // içe aktarma sonucu uyarısı (çakışma): bildirim birkaç saniyede kaybolur, bu kutuda kalır
  const [notice, setNotice] = useState<string | null>(null);
  const run = useSave(() => postForm<Record<string, number> & { uyari?: string }>("/api/bank-import", file!, {
    ...(pv!.pdf ? {} : { mapping: JSON.stringify(pv!.mapping), header_row: String(pv!.header_row) }),
    ...(password ? { password } : {}),
    // önizlemede hesap kesin tanındıysa (IBAN / takma ad) o hesap açıkça gönderilir: belgede banka adı yokken (yalnız IBAN)
    // içe aktarma hesabı ayrıca çözmüyor, hareketler hesapsız kalıp çakışma denetimi atlanıyordu
    ...(typeof acc === "number" ? { account_id: String(acc) } : acc === "new" ? { new_account: "true" }
      : pv!.account_match?.status === "exact" ? { account_id: String((pv!.account_match as { account_id: number }).account_id) } : {}),
  }), (r) => {
    const parts = [r.gelir && `${r.gelir} gelir`, r.gider && `${r.gider} gider`, r.maas && `${r.maas} maaş onaylandı`,
      r.odendi_isaretlenen && `${r.odendi_isaretlenen} ekstre ödendi`,
      (r.eslesen_elle || r.eslesen_kural) && `${(r.eslesen_elle ?? 0) + (r.eslesen_kural ?? 0)} kayıt elle girdiğinle eşleşti (iki kez sayılmadı)`,
      r.eslesme_sorusu && `${r.eslesme_sorusu} eşleşme sorusu Yapılacaklar'da`,
      r.virman && `${r.virman} ${TERIM.kendiHesaplar.ad.toLocaleLowerCase("tr")}`, r.tekrar && `${r.tekrar} zaten vardı`,
      r.cakisma && `${r.cakisma} başka belgeden zaten yüklüydü`].filter(Boolean);
    ui.toast(parts.length ? parts.join(" · ") : "Yeni hareket yok");
    setNotice(typeof r.uyari === "string" && r.uyari ? r.uyari : null);
    setPv(null);
    setFile(null);
    onDone?.();
  });
  const setCol = (k: Key, v: string) => {
    if (!pv || !file) return;
    const mapping = { ...pv.mapping, [k]: v === "" ? null : Number(v) };
    if (k === "amount" && v !== "") Object.assign(mapping, { debit: null, credit: null });
    if ((k === "debit" || k === "credit") && v !== "") mapping.amount = null;
    preview(file, mapping, pv.header_row);
  };

  if (incoming !== undefined && !file && !busy && !err && !notice) return null;  // tek yükleme alanından gelen bir şey yok
  return (
    <section className="card space-y-3 p-4">
      {full && <QuotaFullModal onClose={() => setFull(null)} upgrade={full.upgrade}
        manual={(["gider", "gelir"] as const).map((kind) => ({
          label: kind === "gider" ? "Elle gider" : "Elle gelir",
          onClick: () => { setFull(null); ui.openEntry(undefined, { kind }); },
        }))} />}
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Landmark size={18} /> Hesap dökümü{file ? `: ${file.name}` : ""}</h2>
        <p className="text-xs text-stone-500">
          Maaş sabit kaleme bağlanır, kredi kartı ödemeleri ilgili ekstreyi "ödendi" yapar, kendi hesapların arası aktarımlar
          gelir/gider sayılmaz, aynı hareket iki kez eklenmez, hesabın bakiyesi güncellenir.
          {busy && " PDF yapay zekâyla okunuyor; her satırın bakiyesi bir öncekiyle karşılaştırılıyor (bir dakika sürebilir)…"}
        </p>
        {notice && (
          <div className="mt-2 flex gap-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">İçe aktarıldı. {notice}</span>
            <button className="btn-ghost -m-1 shrink-0 p-1" aria-label="Uyarıyı kapat" onClick={() => setNotice(null)}><X size={14} /></button>
          </div>
        )}
      </div>
      {incoming === undefined && <>
        <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={busy}
          onClick={() => (quota.full ? setFull({}) : input.current?.click())}>
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Landmark size={16} />} {busy ? "Okunuyor…" : "Dosya seç (.pdf, .xlsx, .xls, .csv)"}
        </button>
        <input ref={input} type="file" accept=".pdf,application/pdf,.xlsx,.xls,.csv,text/csv" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { setFile(f); preview(f); } }} />
      </>}
      {busy && <Loader2 size={18} className="animate-spin text-blue-600" />}
      {err && <p className="text-sm text-red-600">{err}</p>}

      {pv && file && (
        <div className="space-y-3">
          {pv.pdf ? (
            <p className={cx("text-sm", pv.verified ? "text-green-700 dark:text-green-400" : "text-amber-700 dark:text-amber-400")}>
              {pv.bank && <b>{pv.bank} · </b>}
              {pv.verified ? "Bakiye zinciriyle doğrulandı ✓ (her satırda önceki bakiye + tutar = bakiye)"
                : `Doğrulanamadı: ${pv.chain_bad?.length ?? 0} satırda bakiye tutmuyor. Satırları kontrol et ya da tekrar yükle.`}
            </p>
          ) : (<>
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
          </>)}
          {pv.account_match?.status && (
            <AccountQuestion m={pv.account_match as AccountMatch} value={acc} onChange={setAcc} />
          )}
          {pv.balance_change && !pv.checks?.length && (
            <p className="text-xs text-stone-500">
              {pv.balance_change.account} bakiyesi {tl(pv.balance_change.current)} → <b>{tl(pv.balance_change.new)}</b> ({dateTR(pv.balance_change.date)} itibarıyla)
            </p>
          )}
          {!!pv.checks?.length && (
            <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
              {pv.checks.map((c) => <p key={c}>⚠️ {c}</p>)}
              <label className="flex items-center gap-2 font-medium">
                <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} />
                Kontrol ettim, içe aktar (yanlışsa Ekstreler → Son yüklemeler'den geri alınır)
              </label>
            </div>
          )}
          {pv.warning && (
            <p className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
              ⚠️ {pv.warning}
            </p>
          )}
          {pv.overlap && pv.overlap.count > 0 && (
            <p className="flex gap-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="note">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
              <span>{pv.overlap.count} hareket ({dateTR(pv.overlap.from)} – {dateTR(pv.overlap.to)}) başka bir belgeden (aynı dönemin PDF'i ya da
                Excel'i) zaten yüklü; aynı gün ve tutarla kayıtlı olduğu için tekrar eklenmeyecek.</span>
            </p>
          )}
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(pv.counts).map(([k, n]) => (
              <span key={k} className={cx("rounded-full px-2 py-0.5", k === "tekrar" || k === "virman" ? "bg-stone-100 dark:bg-stone-800"
                : k === "cakisma" ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300" : "bg-blue-50 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300")}>
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
                    <td className="min-w-36 px-2 py-1 whitespace-normal [overflow-wrap:anywhere] sm:min-w-64" title={r.description}>{r.who ? <b>{maskText(r.who)}</b> : maskText(r.description)}</td>
                    <td className={cx("px-2 py-1 text-right tabular-nums", r.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(r.amount)}</td>
                    <td className="px-2 py-1 text-stone-500">{LABEL[r.type] ?? r.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={!pv.total || run.isPending || (!!pv.checks?.length && !sure)}
              onClick={() => run.mutate(undefined, { onError: (e) => { onFail(e); void quota.refresh(); } })}>
              {pv.total} hareketi içe aktar
            </button>
            <button className="btn-ghost" onClick={() => { setPv(null); setFile(null); setErr(null); onDone?.(); }}>Vazgeç</button>
          </div>
        </div>
      )}
    </section>
  );
}
