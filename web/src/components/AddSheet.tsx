import { Camera, FileText, Loader2, Minus, Plus, Repeat } from "lucide-react";
import type { BillDraft } from "../api";
import { useRef, useState } from "react";
import { useMeta } from "./hooks";
import Modal from "./Modal";
import type { EntryDraft } from "../ui";

/** Fotoğrafı yüklemeden önce küçült: mobil fotoğraflar 5-12 MB, 1600px yeterli ve token maliyeti düşer. */
async function shrink(file: File, max = 1600): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("Görüntü işlenemedi"))), "image/jpeg", 0.85));
}

interface DocResult {
  type: "receipt" | "bill";
  // fiş
  merchant?: string;
  date?: string | null;
  total?: number | null;
  payment?: string;
  category?: string;
  items?: { name: string; product?: string | null; qty?: number; unit?: string; amount: number | null; split?: string | null }[];
  address?: string | null;
  legal_name?: string | null;
  // fatura
  provider?: string;
  amount?: number | null;
  due_date?: string | null;
  description?: string;
  file_name?: string;
  confidence: string;
}

export default function AddSheet({ onClose, onPick, onBill }: {
  onClose: () => void;
  onPick: (d: EntryDraft) => void;
  onBill: (d: BillDraft) => void;
}) {
  const meta = useMeta();
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // uzun fiş: sırayla çekilen parçalar tek fiş olarak okunur
  const [pages, setPages] = useState<Blob[]>([]);

  /** Fiş mi fatura mı sunucu (Gemini) karar verir; ona göre doğru form açılır. Birden fazla görüntü = aynı fişin parçaları. */
  async function parse(parts: { blob: Blob; name: string }[]) {
    if (!parts.length) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      parts.slice(0, 4).forEach((p) => fd.append("file", p.blob, p.name));
      const res = await fetch("/api/documents/parse", { method: "POST", body: fd });
      const r = (await res.json()) as DocResult & { detail?: string };
      if (!res.ok) throw new Error(r.detail ?? "Belge okunamadı");
      if (r.type === "bill") {
        onBill({ provider: r.provider, amount: r.amount, due_date: r.due_date, category: r.category,
          description: r.description, file_name: r.file_name, confidence: r.confidence });
        return;
      }
      const items = (r.items ?? []).filter((i) => i.name).slice(0, 6)
        .map((i) => ((i.qty ?? 1) > 1 ? `${i.qty}× ${i.name}` : i.name)).join(", ");
      onPick({
        kind: "gider",
        description: r.merchant,
        amount: r.total ?? undefined,
        entry_date: r.date ?? undefined,
        category: r.category,
        // fişte "kart" yazıyorsa kredi kartı varsay; banka kartıysa kullanıcı formda değiştirir
        payment: r.payment === "nakit" ? "nakit" : r.payment === "kart" ? "kredi_karti" : undefined,
        legal_name: r.legal_name,
        items: r.items,
        location: r.address ?? null,
        note: [items && `Fiş: ${items}`, r.confidence !== "yüksek" && `okuma güveni: ${r.confidence}`].filter(Boolean).join(" · "),
      });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      setPages([]);
    }
  }

  /** Kamera: her fotoğraf parçalara eklenir; "Bitti, oku" ile hepsi birlikte okunur. */
  async function onCamera(f: File | undefined) {
    if (!f) return;
    try {
      setPages([...pages, await shrink(f)]);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  /** Galeri/dosya: PDF tek başına; fotoğraflar (en fazla 4) aynı fişin parçaları sayılır. */
  async function onFiles(list: FileList | null) {
    const files = [...(list ?? [])];
    if (!files.length) return;
    const pdf = files.find((f) => f.type === "application/pdf");
    if (pdf) return parse([{ blob: pdf, name: pdf.name }]);
    try {
      parse(await Promise.all(files.slice(0, 4).map(async (f, i) => ({ blob: await shrink(f), name: `parca${i + 1}.jpg` }))));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const Opt = ({ icon, title, sub, onClick, disabled }: {
    icon: React.ReactNode; title: string; sub: string; onClick: () => void; disabled?: boolean;
  }) => (
    <button onClick={onClick} disabled={disabled}
      className="flex w-full items-center gap-3 rounded-xl border border-stone-200 p-3 text-left hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:hover:bg-stone-800">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-stone-100 dark:bg-stone-800">{icon}</span>
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs text-stone-500">{sub}</span>
      </span>
    </button>
  );

  return (
    <Modal title="Ekle" onClose={onClose}>
      <div className="space-y-2">
        <Opt icon={busy ? <Loader2 className="animate-spin" size={20} /> : <Camera size={20} className="text-blue-600" />}
          title={busy ? "Okunuyor…" : "Fiş / fatura çek"}
          sub={meta.data?.receipt_enabled === false ? "Sunucuda GEMINI_API_KEY tanımlı değil"
            : "Fiş mi fatura mı kendisi anlar; tutar, tarih, işyeri otomatik dolar"}
          onClick={() => cameraRef.current?.click()} disabled={busy || pages.length > 0 || meta.data?.receipt_enabled === false} />
        <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden"
          onChange={(e) => { onCamera(e.target.files?.[0]); e.target.value = ""; }} />
        {pages.length > 0 && !busy && (
          <div className="space-y-2 rounded-xl border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/30">
            <div className="text-sm"><b>{pages.length} fotoğraf</b> hazır. Fiş uzunsa devamını çek (en fazla 4); üst üste binen satırlar bir kez sayılır.</div>
            <div className="flex flex-wrap gap-2">
              {pages.length < 4 && (
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => cameraRef.current?.click()}>
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
          onClick={() => fileRef.current?.click()} disabled={busy || meta.data?.receipt_enabled === false} />
        <input ref={fileRef} type="file" accept="application/pdf,image/*" multiple className="hidden"
          onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
        <Opt icon={<Minus size={20} className="text-red-600" />} title="Gider" sub="Psikolog, nakit ödeme, havale…"
          onClick={() => onPick({ kind: "gider" })} />
        <Opt icon={<Plus size={20} className="text-green-600" />} title="Gelir" sub="Maaş, ek gelir…"
          onClick={() => onPick({ kind: "gelir" })} />
        <Opt icon={<Repeat size={20} className="text-stone-600" />} title="Her ay tekrarlayan" sub="Kira, maaş, aidat — planlamaya otomatik girer"
          onClick={() => onPick({ kind: "gider", recurring: true })} />
        <button className="w-full pt-1 text-center text-xs text-stone-500 underline" onClick={() => onBill({})}>
          Faturayı dosyasız elle gir
        </button>
        {err && <p className="pt-1 text-sm text-red-600">{err}</p>}
      </div>
    </Modal>
  );
}
