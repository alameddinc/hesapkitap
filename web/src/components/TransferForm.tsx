// Transfer ekle (mobil screens/Transferler.tsx AddTransfer ile aynı akış ve metinler).
// Elle: kişiye gönderilen / kişiden gelen para → POST /api/entries (counterparty); sunucu açık borç/alacakla aynı tutarsa kapatır.
// Dekonttan (yetenek dekont_parse): POST /api/dekont/parse (foto/PDF → taslak, kaydetmez) formu doldurur; kayıt
// POST /api/dekont/save ile tek işlem olur, dekont dosyası ekli; hesap dökümü gelince aynı işlemle birleşir.
// Aynı dekont kayıtlıysa 409 "Bu dekont zaten eklenmiş" + entry_id → "Kayda git".
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Camera, FileText, Info, Receipt, Users2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { ApiError, call, send } from "../lib/apiError";
import { cx, parseAmount, tl, todayISO } from "../format";
import { useUI } from "../ui";
import { shrink } from "./AddSheet";
import { useAccounts } from "./AccountSelect";
import type { Debt } from "./Debts";
import { isQuotaFull, QuotaFullModal, ReadsLine, useDocQuota, type QuotaFull } from "./DocQuota";
import { useCap, useOpenEntry, useSave } from "./hooks";
import Modal from "./Modal";
import { ReportProblem } from "./Feedback";
import Segmented from "./Segmented";

export type Yon = "giden" | "gelen";
interface Party { name: string | null; iban_last4: string | null; bank: string | null; is_me: boolean }
export interface DekontResult {
  type: "dekont";
  /** null: dekontta kullanıcının hesabı/adı bulunamadı ya da iki taraf da kullanıcı → formda iki seçenek */
  direction: Yon | null;
  amount: number | null;
  date: string | null;
  counterparty: string | null;
  counterparty_known?: boolean | null;
  /** karşı tarafın IBAN'ının son 4 hanesi (tamamı hiçbir zaman gelmez) */
  iban_last4?: string | null;
  bank?: string | null;
  /** havale mesajı / açıklama: kayıt açıklamasında korunur */
  description?: string;
  transfer_type?: "havale" | "eft" | "fast" | null;
  ref?: string;
  account_hint?: number | null;
  own_transfer?: boolean;
  parties?: { sender: Party; receiver: Party };
  duplicate?: { entry_id: number; message: string };
  dekont_file?: string | null;
  source?: "metin" | "yapay_zeka" | null;
  unreadable?: boolean;
  not_transfer?: boolean;
  message?: string;
}

export const isPdf = (f: File) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);

/** Dekontu oku: fotoğraf küçültülür (fiş gibi), PDF olduğu gibi gider. Hak doluysa QuotaFull gövdesiyle ApiError fırlar. */
export async function parseDekont(f: File): Promise<DekontResult> {
  const fd = new FormData();
  if (isPdf(f)) fd.append("file", f.type ? f : new Blob([f], { type: "application/pdf" }), f.name || "dekont.pdf");
  else fd.append("file", await shrink(f), "dekont.jpg");
  return send<DekontResult>("/api/dekont/parse", { method: "POST", body: fd });
}

/** PDF metninden öneri (yapay zekâsız, hak düşmez): dekont | ekstre | null. Hata olursa null. */
export async function classifyPdf(f: File): Promise<"dekont" | "ekstre" | null> {
  const fd = new FormData();
  fd.append("file", f, f.name || "belge.pdf");
  try { return (await send<{ suggest: "dekont" | "ekstre" | null }>("/api/documents/classify", { method: "POST", body: fd })).suggest; } catch { return null; }
}

/** Ad karşılaştırması (sunucu db._name_key gibi): Türkçe harf ve boşluk farkı sayılmaz. */
const nameKey = (s: string) => s.toLocaleUpperCase("tr").replace(/İ/g, "I").replace(/Ş/g, "S").replace(/Ğ/g, "G")
  .replace(/Ü/g, "U").replace(/Ö/g, "O").replace(/Ç/g, "C").replace(/[^A-Z]/g, "");
const dirKind = (d: Yon | null | undefined): "gider" | "gelir" | "" => (d === "giden" ? "gider" : d === "gelen" ? "gelir" : "");
const amountText = (n: number | null | undefined) => (n ? String(n).replace(".", ",") : "");
const dupOf = (e: unknown): number | null => {
  const b = e instanceof ApiError && e.status === 409 ? (e.body as { entry_id?: unknown } | null) : null;
  return typeof b?.entry_id === "number" ? b.entry_id : null;
};

interface TransfersData { people: { name: string; category: string | null }[]; debts: Debt[] }

export default function TransferForm({ initial, onClose }: { initial?: DekontResult | null; onClose: () => void }) {
  const ui = useUI();
  const dekontOn = useCap("dekont_parse");
  const quota = useDocQuota();
  const openEntry = useOpenEntry();
  const [dk, setDk] = useState<DekontResult | null>(initial ?? null);
  const fromDk = !!initial && !initial.unreadable;
  const [dir, setDir] = useState<"gider" | "gelir" | "">(initial ? (initial.unreadable ? "gider" : dirKind(initial.direction)) : "gider");
  const [person, setPerson] = useState(fromDk ? initial!.counterparty ?? "" : "");
  const [amount, setAmount] = useState(fromDk ? amountText(initial!.amount) : "");
  const [date, setDate] = useState((fromDk && initial!.date) || todayISO());
  const [acc, setAcc] = useState<number | null>(fromDk ? initial!.account_hint ?? null : null);
  const [note, setNote] = useState(fromDk ? initial!.description ?? "" : "");
  const [debtAns, setDebtAns] = useState<"evet" | "hayir" | null>(null);
  const [dup, setDup] = useState<number | null>(initial?.duplicate?.entry_id ?? null);
  const [reading, setReading] = useState(false);
  const [full, setFull] = useState<Partial<QuotaFull> | null>(null);
  const [readErr, setReadErr] = useState<string | null>(initial?.unreadable ? initial.message || "Dekont okunamadı; bilgileri elle gir." : null);
  const fileRef = useRef<HTMLInputElement>(null);
  const tr = useQuery({ queryKey: ["transfers", "form"], queryFn: () => send<TransfersData>("/api/transfers"), retry: false });
  const accs = useAccounts();
  const banks = (accs.data?.accounts ?? []).filter((a) => a.kind === "banka" || a.kind === "nakit");
  const names = useMemo(() => {
    const seen = new Map<string, string>();
    for (const n of [...(tr.data?.people ?? []).map((p) => p.name), ...(tr.data?.debts ?? []).map((d) => d.person)]) {
      const k = n.trim().toLocaleLowerCase("tr");
      if (k && !seen.has(k)) seen.set(k, n.trim());
    }
    return [...seen.values()];
  }, [tr.data]);
  const q = person.trim().toLocaleLowerCase("tr");
  const known = tr.data?.people.find((p) => p.name.toLocaleLowerCase("tr") === q);
  const amt = parseAmount(amount);
  // dekontta karşı tarafın açık kaydı: gelen ↔ alacak, giden ↔ borç; tutar kalanı aşmıyorsa "borç ödemesi mi?" (kuruşla)
  const open = useMemo(() => {
    if (!dk || !dir || !(amt > 0) || !person.trim()) return null;
    const want = dir === "gelir" ? "alacak" : "borc";
    const k = nameKey(person);
    return (tr.data?.debts ?? []).find((d) => d.asset === "TRY" && d.direction === want && !d.settled_at && !d.written_off_at
      && nameKey(d.person) === k && Math.round(d.remaining * 100) > 0 && Math.round(amt * 100) <= Math.round(d.remaining * 100)) ?? null;
  }, [dk, dir, amt, person, tr.data]);
  const ok = person.trim().length > 0 && amt > 0 && !!dir && dup == null;

  function apply(d: DekontResult) {
    setDk(d);
    setDebtAns(null);
    setDup(d.duplicate?.entry_id ?? null);
    setReadErr(d.unreadable ? d.message || "Dekont okunamadı; bilgileri elle gir." : null);
    if (d.unreadable) return; // dosya ekli kalır, alanlar elle
    setDir(dirKind(d.direction));
    setPerson(d.counterparty ?? "");
    if (d.amount) setAmount(amountText(d.amount));
    if (d.date) setDate(d.date);
    setAcc(d.account_hint ?? null);
    setNote(d.description ?? "");
  }
  /** Yön dekonttan çıkmadıysa: seçilen yöne göre karşı taraf (gönderdim → alıcı, bana geldi → gönderen). */
  function pickDir(k: "gider" | "gelir") {
    setDir(k);
    const pt = dk?.parties;
    if (!pt || dk?.direction) return;
    const auto = [pt.sender.name, pt.receiver.name].filter(Boolean);
    if (!person.trim() || auto.includes(person)) setPerson((k === "gider" ? pt.receiver.name : pt.sender.name) ?? "");
  }
  async function read(f: File | undefined) {
    if (!f) return;
    setReading(true);
    setReadErr(null);
    try {
      apply(await parseDekont(f));
    } catch (e) {
      if (e instanceof ApiError && isQuotaFull(e.body)) setFull(e.body);
      else setReadErr((e as Error).message || "Dekont okunamadı");
    } finally {
      setReading(false);
      void quota.refresh();
    }
  }
  const pick = () => { if (quota.fullFor("belge")) setFull({ kind: "belge" }); else fileRef.current?.click(); };

  const save = useSave(() => {
    const who = person.trim();
    if (dk) {
      return call<{ id: number; debt_settled: number | null }>("POST", "/api/dekont/save", {
        date, amount: amt, direction: dir === "gider" ? "giden" : "gelen", counterparty: who,
        account_id: acc, description: note.trim() || null, ref: dk.ref || null,
        transfer_type: dk.transfer_type ?? null, bank: dk.bank ? dk.bank.slice(0, 60) : null, dekont_file: dk.dekont_file ?? null,
        debt_id: debtAns === "evet" && open ? open.id : null, debt_link: debtAns !== "hayir",
      });
    }
    return call<{ id: number; debt_settled: number | null }>("POST", "/api/entries", {
      entry_date: date, kind: dir, amount: amt, counterparty: who,
      description: note.trim() || (dir === "gider" ? `${who} · gönderildi` : `${who} · geldi`),
      note: note.trim() || null, category: known?.category ?? null,
      payment: banks.find((a) => a.id === acc)?.kind === "nakit" ? "nakit" : "havale", account_id: acc,
    });
  }, (r) => {
    ui.toast(r.debt_settled ? (dk ? "Eklendi · borç/alacak kaydına işlendi" : "Eklendi · açık borç/alacak kapandı") : "Transfer eklendi");
    onClose();
  }, { inline: true });
  const saveErr = save.error && dupOf(save.error) == null ? save.error.message : null;
  const doSave = () => save.mutate(undefined, { onError: (e) => { const id = dupOf(e); if (id != null) setDup(id); } });

  if (full) return (
    <QuotaFullModal onClose={() => setFull(null)} upgrade={full.upgrade} info={full}
      manual={[{ label: "Elle transfer ekle", onClick: () => setFull(null) }]} />
  );

  const pdf = !!dk?.dekont_file?.endsWith(".pdf");
  const sub = dk ? [dk.transfer_type ? dk.transfer_type.toUpperCase().replace("HAVALE", "Havale") : null, dk.bank,
    dk.iban_last4 ? `IBAN …${dk.iban_last4}` : null].filter(Boolean).join(" · ") : "";
  return (
    <Modal title="Transfer ekle" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={!ok || save.isPending || reading} onClick={doSave}>{save.isPending ? "Kaydediliyor…" : "Kaydet"}</button>
    </>}>
      <div className="space-y-3 text-sm">
        {dekontOn && (
          <>
            <input ref={fileRef} type="file" accept="image/*,application/pdf" className="hidden" aria-hidden tabIndex={-1}
              onChange={(e) => { void read(e.target.files?.[0]); e.target.value = ""; }} />
            <button className="btn-ghost w-full justify-center border border-stone-300 py-2 dark:border-stone-700" disabled={reading || save.isPending} onClick={pick}>
              <Camera size={16} /> {reading ? "Okunuyor…" : dk ? "Başka dekonttan doldur" : "Dekonttan doldur"}
            </button>
            {!dk && <ReadsLine className="-mt-1 text-center text-xs text-stone-500" />}
          </>
        )}
        {dk && (
          <div data-testid="dekont-ekli" className="flex items-center gap-3 rounded-xl border border-stone-200 px-3 py-2 dark:border-stone-700">
            <span className={cx("flex size-9 shrink-0 items-center justify-center rounded-lg", pdf ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400" : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300")}>
              {pdf ? <FileText size={18} aria-hidden /> : <Receipt size={18} aria-hidden />}</span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold">{dk.dekont_file ? `Dekont ekli${pdf ? " (PDF)" : " (fotoğraf)"}` : "Dekonttan okundu"}</span>
              <span className="block text-xs break-words text-stone-500">{sub || "Hesap dökümü gelince aynı işlemle birleşir"}</span>
            </span>
          </div>
        )}
        {readErr && <Note kind="warn">{readErr}
          <span className="mt-1 block"><ReportProblem subject="Dekont okunamadı" error={readErr} code="dekont_unreadable" /></span></Note>}
        {dup != null && (
          <div role="alert" data-testid="dekont-zaten" className="flex items-center gap-2 rounded-lg bg-amber-50 p-2.5 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <AlertTriangle size={16} className="shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">Bu dekont zaten eklenmiş.</span>
            <button className="font-semibold text-blue-700 hover:underline dark:text-blue-300"
              onClick={() => { const id = dup; onClose(); void openEntry(id, date, (row) => ui.openEntry(row)).then((found) => { if (!found) ui.toast("Kayıt bulunamadı"); }); }}>Kayda git</button>
          </div>
        )}
        {dk?.not_transfer && <Note kind="warn">Bu belge havale/EFT dekontu gibi görünmüyor; bilgileri kontrol et.</Note>}
        <Segmented label="Yön" value={dir} onChange={(v) => v && pickDir(v)}
          options={[{ value: "gider", label: "Gönderdim", tone: "red" }, { value: "gelir", label: "Bana geldi", tone: "green" }]} />
        {!dir && <p className="text-xs text-amber-700 dark:text-amber-400">Yönü seç: dekontta senin hesabın ya da adın bulunamadı.</p>}
        <div>
          <label className="label" htmlFor="tf-person">Kişi</label>
          <input id="tf-person" className="input" list="tf-people" value={person} onChange={(e) => setPerson(e.target.value)} placeholder="Ad"
            autoComplete="off" autoFocus={!dk} />
          <datalist id="tf-people">{names.map((n) => <option key={n} value={n} />)}</datalist>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="tf-amt">Tutar</label>
            <input id="tf-amt" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" />
          </div>
          <div>
            <label className="label" htmlFor="tf-date">Tarih</label>
            <input id="tf-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
        </div>
        {banks.length > 0 && (
          <div>
            <label className="label" htmlFor="tf-acc">Hesap</label>
            <select id="tf-acc" className="input" value={acc ?? ""} onChange={(e) => setAcc(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Seçme</option>
              {banks.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="tf-note">{dk ? "Açıklama" : "Not"}</label>
          <input id="tf-note" className="input" maxLength={dk ? 200 : 120} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder={dk ? "Havale mesajı" : "ör. Kira payı"} />
        </div>
        {open && (
          <div role="group" aria-label="Borç / alacak ödemesi mi?" data-testid="dekont-borc" className="space-y-2 rounded-xl border border-stone-200 p-3 dark:border-stone-700">
            <p className="font-semibold">Borç / alacak ödemesi mi?</p>
            <p className="text-xs break-words text-stone-500">{open.person} · açık {open.direction === "alacak" ? "alacağın" : "borcun"} {tl(open.remaining)}.
              Evet dersen o kayda işlenir; gelir/gider sayılmaz.</p>
            <Segmented label="Borç ödemesi mi" value={debtAns ?? ""} onChange={(v) => v && setDebtAns(v)}
              options={[{ value: "evet", label: "Evet, kayda işle" }, { value: "hayir", label: "Hayır" }]} />
          </div>
        )}
        <p className="flex gap-1.5 text-xs text-stone-500">
          {dk ? <Info size={14} className="mt-px shrink-0" aria-hidden /> : <Users2 size={14} className="mt-px shrink-0" aria-hidden />}
          {dk ? "Tek işlem olarak eklenir; hesap dökümü gelince aynı işlemle birleşir, iki kez sayılmaz." : "Açık bir borç/alacakla aynı tutardaysa o kayıt kendiliğinden kapanır."}</p>
        {saveErr && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{saveErr}</p>}
      </div>
    </Modal>
  );
}

function Note({ kind, children }: { kind: "warn" | "info"; children: React.ReactNode }) {
  return (
    <div role={kind === "warn" ? "alert" : "status"} className={kind === "warn"
      ? "flex gap-2 rounded-lg bg-amber-50 p-2.5 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
      : "flex gap-2 rounded-lg bg-blue-50 p-2.5 text-blue-900 dark:bg-blue-950/40 dark:text-blue-200"}>
      {kind === "warn" ? <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden /> : <Info size={16} className="mt-0.5 shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}
