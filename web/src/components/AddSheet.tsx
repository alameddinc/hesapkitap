import { hataMetni } from "../lib/hataAcikla";
import { HataSatiri } from "./HataKutusu";
import { ArrowLeftRight, Camera, FileText, Info, Loader2, Minus, Plus, Repeat } from "lucide-react";
import { bodyOf, failureText, UNEXPECTED } from "../lib/apiError";
import type { BillDraft } from "../api";
import { useEffect, useRef, useState } from "react";
import { DURUM } from "../lib/durum";
import { okumaSakla, okumaSil, type BekleyenOkuma } from "../lib/okumaKutusu";
import { useCap, useMeta, useDemo } from "./hooks";
import { ReportProblem } from "./Feedback";
import { classifyPdf, parseDekont, type DekontResult } from "./TransferForm";
import { ApiError } from "../lib/apiError";
import Modal from "./Modal";
import { isQuotaFull, QuotaFullModal, ReadsLine, useDocQuota, type QuotaFull } from "./DocQuota";
import type { EntryDraft } from "../ui";
import type { ReceiptCheck } from "./receipt/check";
import { hasTax, taxFields } from "./receipt/TaxCard";

/** Fotoğrafı yüklemeden önce küçült: mobil fotoğraflar 5-12 MB, 1600px yeterli ve token maliyeti düşer. */
export async function shrink(file: File, max = 1600): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("Görüntü işlenemedi"))), "image/jpeg", 0.85));
}

export interface DocResult {
  type: "receipt" | "bill";
  // fiş
  merchant?: string;
  date?: string | null;
  total?: number | null;
  payment?: string;
  category?: string;
  items?: { name: string; product?: string | null; qty?: number; unit?: string; amount: number | null; split?: string | null; unit_price?: number | null }[];
  reread?: { tried: boolean; used: boolean; model?: string };
  check?: ReceiptCheck; // kalem denetimi; karışan rakam önerileri (yetenek digit_fix)
  address?: string | null;
  branch?: string | null;
  payment_card?: string | null;
  installments?: number;
  card_last4?: string | null;
  legal_name?: string | null;
  // yetenek receipt_tax: KDV, KDV dökümü, VKN, vergi dairesi
  vat_total?: number | null;
  vat_lines?: { rate: number; amount: number }[] | null;
  tax_no?: string | null;
  tax_office?: string | null;
  total_check?: "ok" | "kontrol" | null; // yetenek total_check
  receipt_file?: string | null; // saklanan fiş fotoğrafının içerik adı: kayda bağlanır
  storage_warning?: string;
  // fatura
  provider?: string;
  amount?: number | null;
  due_date?: string | null;
  description?: string;
  file_name?: string;
  confidence: string;
  cached?: boolean; // aynı belge daha önce okunmuştu: hak düşmedi
}

/** Okunan fişi forma taslak olarak çevir (AddSheet ve telefondan paylaşılanlar). */
export function docToDraft(r: DocResult, photo?: Blob | null): EntryDraft {
  const items = (r.items ?? []).filter((i) => i.name).slice(0, 6)
        .map((i) => ((i.qty ?? 1) > 1 ? `${i.qty}× ${i.name}` : i.name)).join(", ");
  return {
        kind: "gider",
        description: r.merchant,
        amount: r.total ?? undefined,
        entry_date: r.date ?? undefined,
        category: r.category,
        // fişte "kart" yazıyorsa kredi kartı varsay; banka kartıysa kullanıcı formda değiştirir
        payment: r.payment === "nakit" ? "nakit" : r.payment === "kart" || (r.installments ?? 1) > 1 ? "kredi_karti" : r.payment === "yemek_karti" ? "kart" : undefined,
        wallet_hint: r.payment === "yemek_karti" ? r.payment_card ?? "yemek" : null,
        installments: r.installments && r.installments > 1 ? r.installments : undefined,
        card_last4: r.card_last4 ?? null,
        legal_name: r.legal_name,
        receipt_file: r.receipt_file ?? null,
        items: r.items,
        photo_url: photo?.type.startsWith("image/") ? URL.createObjectURL(photo) : null,
        reread_used: !!r.reread?.used,
        location: r.address ?? null,
        branch: r.branch ?? null,
        cached: !!r.cached,
        check: r.check ?? null,
        tax: hasTax(r) ? taxFields(r) : undefined,
        total_check: r.total_check ?? null,
        note: [items && `Fiş: ${items}`, r.confidence !== "yüksek" && `okuma güveni: ${r.confidence}`].filter(Boolean).join(" · "),
  };
}

/** Ekle ekranındaki seçenek kartı. */
function Opt({ icon, title, sub, onClick, disabled }: {
  icon: React.ReactNode; title: string; sub: string; onClick: () => void; disabled?: boolean;
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="flex w-full items-center gap-3 rounded-xl border border-stone-200 p-3 text-left hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:hover:bg-stone-800">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-stone-100 dark:bg-stone-800">{icon}</span>
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs text-stone-500">{sub}</span>
      </span>
    </button>
  );
}

export default function AddSheet({ onClose, onPick, onBill, onTransfer, okuma }: {
  /** bağlantı yokken saklanan fiş/fatura (lib/okumaKutusu): "Şimdi oku" ile açıldı, hemen okunur */
  okuma?: BekleyenOkuma;
  onClose: () => void;
  onPick: (d: EntryDraft) => void;
  onBill: (d: BillDraft) => void;
  /** Havale/EFT dekontu okundu (ya da elle transfer): Transfer ekle formu */
  onTransfer?: (d: DekontResult | null) => void;
}) {
  const meta = useMeta();
  const dekontOn = useCap("dekont_parse") && !!onTransfer;
  const dekontRef = useRef<HTMLInputElement>(null);
  // dosya fiş/fatura diye seçildi ama PDF metni havale/EFT dekontu (documents/classify, hak düşmez): önce sorulur
  const [redirect, setRedirect] = useState<File | null>(null);
  const [fullDekont, setFullDekont] = useState(false);
  const [errKind, setErrKind] = useState<"fis" | "dekont">("fis"); // hata hangi okumadan (Sorun bildir konusu) // hak doldu uyarısı dekont için: elle transfer
  const demo = useDemo().data;
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // hazır sürüm: belge okuma hakkı doldu (ön kontrol ya da sunucu reddi) → elle ekleme uyarısı
  const quota = useDocQuota();
  const [full, setFull] = useState<Partial<QuotaFull> | null>(null);
  // kamera: fotoğraf fiş sayacına düşer; dosya fiş de fatura da olabilir: ikisi de bittiyse açılmaz (gerisini sunucu söyler)
  const pick = (ref: React.RefObject<HTMLInputElement | null>) => {
    const camera = ref === cameraRef;
    if (camera ? quota.fullFor("fis") : quota.allFull) return setFull({ kind: camera || quota.fullFor("fis") ? "fis" : "belge" });
    ref.current?.click();
  };

  // uzun fiş: sırayla çekilen parçalar tek fiş olarak okunur
  const [pages, setPages] = useState<Blob[]>([]);
  // bağlantısız seçilen belge saklandı: bilgi satırı
  const [stored, setStored] = useState<string | null>(null);
  // "Şimdi oku" (tek dokunuş): saklanan belge hemen okunur
  const okundu = useRef(false);
  useEffect(() => {
    if (!okuma || okundu.current) return;
    okundu.current = true;
    void parse(okuma.files, okuma.id);
  }, [okuma]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Fiş mi fatura mı sunucu (Gemini) karar verir; ona göre doğru form açılır. Birden fazla görüntü = aynı fişin parçaları. */
  async function parse(parts: { blob: Blob; name: string }[], sakli?: string) {
    if (!parts.length) return;
    setErr(null);
    setStored(null);
    // bağlantı yok: okunmaz (okuma hakkı harcanmaz), cihazda saklanır; bağlanınca "Şimdi oku" (components/Bekleyen)
    if (navigator.onLine === false) {
      if (!sakli) await okumaSakla("fis", parts.slice(0, 4));
      setPages([]);
      setStored(parts.some((p) => p.blob.type === "application/pdf") ? DURUM.okumaBekliyorDosya : DURUM.okumaBekliyor);
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      parts.slice(0, 4).forEach((p) => fd.append("file", p.blob, p.name));
      const res = await fetch("/api/documents/parse", { method: "POST", body: fd });
      if (sakli) void okumaSil(sakli); // sunucuya ulaştı: saklanan kopya artık gerekmez
      await handle(res, parts[0]?.blob);
    } catch (e) {
      setErrKind("fis");
      setErr(hataMetni(e));
    } finally {
      setBusy(false);
      setPages([]);
      void quota.refresh();
    }
  }

  /** Demo: örnek fiş, gerçek okuma yolundan (yerel OCR). */
  async function sample(key: string) {
    setBusy(true);
    setErr(null);
    try {
      await handle(await fetch(`/api/demo/sample/${key}`, { method: "POST" }));
    } catch (e) {
      setErr(hataMetni(e));
    } finally {
      setBusy(false);
    }
  }

  /** Okunan belge: faturaysa fatura formu, fişse gider formu (taslakla). */
  async function handle(res: Response, photo?: Blob) {
    // vekilin HTML hata sayfası (413/504) "Unexpected token '<'" olarak görünmesin: gövde güvenli okunur
    const body = await bodyOf(res);
    if (isQuotaFull(body)) {
      setFull(body);
      return;
    }
    if (!res.ok) throw new Error(failureText(res.status, body));
    if (!body) throw new Error(UNEXPECTED);
    const r = body as DocResult;
    if (r.type === "bill") {
      onBill({ provider: r.provider, amount: r.amount, due_date: r.due_date, category: r.category,
        description: r.description, file_name: r.file_name, confidence: r.confidence, cached: !!r.cached });
      return;
    }
    onPick(docToDraft(r, photo));
  }

  /** Kamera: her fotoğraf parçalara eklenir; "Bitti, oku" ile hepsi birlikte okunur. */
  async function onCamera(f: File | undefined) {
    if (!f) return;
    try {
      setPages([...pages, await shrink(f)]);
    } catch (e) {
      setErr(hataMetni(e));
    }
  }

  /** Havale/EFT dekontu (foto/PDF) → Transfer ekle formu dolu açılır; okunamadıysa dosya ekli, alanlar boş. */
  async function readDekont(f: File | undefined) {
    if (!f || !onTransfer) return;
    setBusy(true);
    setErr(null);
    try {
      onTransfer(await parseDekont(f));
    } catch (e) {
      if (e instanceof ApiError && isQuotaFull(e.body)) { setFullDekont(true); setFull(e.body); }
      else { setErrKind("dekont"); setErr(hataMetni(e, "Dekont okunamadı")); }
    } finally {
      setBusy(false);
      setRedirect(null);
      void quota.refresh();
    }
  }

  /** Galeri/dosya: PDF tek başına; fotoğraflar (en fazla 4) aynı fişin parçaları sayılır. */
  async function onFiles(list: FileList | null) {
    const files = [...(list ?? [])];
    if (!files.length) return;
    const pdf = files.find((f) => f.type === "application/pdf");
    if (pdf && dekontOn) { // PDF metni dekont gibiyse fatura diye okutmadan sor
      setBusy(true);
      const s = await classifyPdf(pdf);
      setBusy(false);
      if (s === "dekont") return setRedirect(pdf);
    }
    if (pdf) return parse([{ blob: pdf, name: pdf.name }]);
    try {
      parse(await Promise.all(files.slice(0, 4).map(async (f, i) => ({ blob: await shrink(f), name: `parca${i + 1}.jpg` }))));
    } catch (e) {
      setErr(hataMetni(e));
    }
  }


  if (full) return (
    <QuotaFullModal onClose={onClose} upgrade={full.upgrade} hasPhoto={!!full.receipt_file} info={full}
      manual={fullDekont && onTransfer ? [{ label: "Elle transfer ekle", onClick: () => onTransfer(null) }]
        : [{ label: "Elle ekle", onClick: () => onPick({ kind: "gider", receipt_file: full.receipt_file ?? null }) }]} />
  );

  if (redirect) return (
    <Modal title="Havale / EFT dekontu mu?" onClose={() => { if (!busy) setRedirect(null); }}>
      <div data-testid="dekont-yonlendir" className="space-y-3 text-sm">
        <div role="status" className="flex gap-2 rounded-lg bg-blue-50 p-2.5 text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
          <Info size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">Bu bir havale/EFT dekontu gibi görünüyor. Transfer olarak eklersen kişiye giden ya da kişiden gelen para olarak Transferler'e girer.</span>
        </div>
        <button className="btn-primary w-full justify-center py-2" disabled={busy} onClick={() => void readDekont(redirect)}>{busy ? "Okunuyor…" : "Transfer olarak ekle"}</button>
        <button className="btn-ghost w-full justify-center border border-stone-300 py-2 dark:border-stone-700" disabled={busy}
          onClick={() => { const f = redirect; setRedirect(null); void parse([{ blob: f, name: f.name }]); }}>Yine de fatura olarak ekle</button>
        {err && <HataSatiri metin={err} nerede="Belge ekleme" kod="belge_okunamadi" />}
      </div>
    </Modal>
  );

  return (
    <Modal title="Ekle" onClose={onClose}>
      <div className="space-y-2">
        {demo && (
          <div className="space-y-2 rounded-xl border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/30">
            <div className="text-sm"><b>Örnek fiş okut</b> <span className="text-stone-500">· demoda kendi fotoğrafın yüklenmez; anahtarsız yerel okuma</span></div>
            <div className="flex flex-wrap gap-2">
              {demo.samples.filter((x) => x.kind === "receipt").map((x) => (
                <button key={x.key} className="btn-primary px-3 py-1.5 text-sm" disabled={busy} onClick={() => sample(x.key)}>
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />} {x.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {!demo && <>
        <Opt icon={busy ? <Loader2 className="animate-spin" size={20} /> : <Camera size={20} className="text-blue-600" />}
          title={busy ? "Okunuyor…" : "Fiş / fatura çek"}
          sub={meta.data?.receipt_enabled === false ? "Bu sunucuda fiş okuma kapalı; sunucu yöneticisi Ayarlar'dan açabilir"
            : meta.data?.receipt_engine === "yerel" ? "Yerel okuma (anahtarsız): tutar, tarih, işyeri dolar — kontrol et"
            : "Fiş mi fatura mı kendisi anlar; tutar, tarih, işyeri otomatik dolar"}
          onClick={() => pick(cameraRef)} disabled={busy || pages.length > 0 || meta.data?.receipt_enabled === false} />
        <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden"
          onChange={(e) => { onCamera(e.target.files?.[0]); e.target.value = ""; }} />
        {pages.length > 0 && !busy && (
          <div className="space-y-2 rounded-xl border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/30">
            <div className="text-sm"><b>{pages.length} fotoğraf</b> hazır. Fiş uzunsa devamını çek (en fazla 4); üst üste binen satırlar bir kez sayılır.</div>
            <div className="flex flex-wrap gap-2">
              {pages.length < 4 && (
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => pick(cameraRef)}>
                  <Camera size={16} /> Devamını çek
                </button>
              )}
              <button className="btn-primary" onClick={() => parse(pages.map((b, i) => ({ blob: b, name: `parca${i + 1}.jpg` })))}>Bitti, oku</button>
              <button className="btn-ghost" onClick={() => setPages([])}>Vazgeç</button>
            </div>
          </div>
        )}
        <Opt icon={<FileText size={20} className="text-violet-600" />} title="Dosyadan yükle"
          sub="E-fatura PDF'i veya galeriden fiş fotoğrafı (uzun fişse parçalarını birlikte seç)"
          onClick={() => pick(fileRef)} disabled={busy || meta.data?.receipt_enabled === false} />
        <input ref={fileRef} type="file" accept="application/pdf,image/*" multiple className="hidden"
          onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
        {dekontOn && <>
          <Opt icon={<ArrowLeftRight size={20} className="text-teal-600" />} title="Havale / EFT dekontu"
            sub="Gönderdiğin ya da sana gelen para: Transferler'e eklenir"
            onClick={() => { if (quota.fullFor("belge")) { setFullDekont(true); setFull({ kind: "belge" }); } else dekontRef.current?.click(); }} disabled={busy} />
          <input ref={dekontRef} type="file" accept="image/*,application/pdf" className="hidden"
            onChange={(e) => { void readDekont(e.target.files?.[0]); e.target.value = ""; }} />
        </>}
        <ReadsLine className="px-1 text-xs text-stone-500" />
        </>}
        <Opt icon={<Minus size={20} className="text-red-600" />} title="Gider" sub="Psikolog, nakit ödeme, havale…"
          onClick={() => onPick({ kind: "gider" })} />
        <Opt icon={<Plus size={20} className="text-green-600" />} title="Gelir" sub="Maaş, ek gelir…"
          onClick={() => onPick({ kind: "gelir" })} />
        <Opt icon={<Repeat size={20} className="text-stone-600" />} title="Her ay tekrarlayan" sub="Kira, maaş, aidat — planlamaya otomatik girer"
          onClick={() => onPick({ kind: "gider", recurring: true })} />
        <button className="w-full pt-1 text-center text-xs text-stone-500 underline" onClick={() => onBill({})}>
          Faturayı dosyasız elle gir
        </button>
        {stored && (
          <p role="status" data-testid="okuma-saklandi" className="flex gap-2 rounded-lg bg-blue-50 p-2.5 text-sm text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
            <Info size={16} className="mt-0.5 shrink-0" aria-hidden /><span className="min-w-0 flex-1">{stored}</span></p>
        )}
        {err && (
          <div className="space-y-1 pt-1" role="alert">
            <p className="text-sm text-red-600 dark:text-red-400">{err}</p>
            <ReportProblem subject={errKind === "dekont" ? "Dekont okunamadı" : "Fiş okunamadı"} error={err} code={errKind === "dekont" ? "dekont_unreadable" : "receipt_unreadable"} />
          </div>
        )}
      </div>
    </Modal>
  );
}
