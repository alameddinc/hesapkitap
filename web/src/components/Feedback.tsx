import { CheckCircle2, ImagePlus, MessageSquareWarning, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { call } from "../lib/apiError";
import Modal from "./Modal";
import { useMeta } from "./hooks";

// Uygulama içi "Sorun bildir" (sunucu yeteneği "feedback"): mesaj + en çok 3 ekran görüntüsü yöneticiye gider (GitHub'a
// değil). POST /api/feedback multipart: message, screenshots, context (JSON: route, platform, error, code, book).
// Giriş ekranından (oturumsuz), demodan ve ortak defter üyesinden de gönderilir; sunucu saatte 10 ile sınırlar.

const MAX_MESSAGE = 2000;
const MAX_SHOTS = 3;
const MAX_MB = 5;
const TYPES = ["image/jpeg", "image/png", "image/webp"];

export interface FeedbackContext { error?: string; code?: string }

const bookOf = () => (/(?:^|;\s*)hk_book=kendi/.test(document.cookie) ? "kendi" : "ortak");

/** Sorun bildir penceresi. context: hata ekranından açıldıysa hata metni / kodu da gider. */
export default function FeedbackDialog({ onClose, context, title = "Sorun bildir" }: {
  onClose: () => void; context?: FeedbackContext; title?: string;
}) {
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const urls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);

  const add = (list: FileList | null) => {
    setErr(null);
    const picked = Array.from(list ?? []);
    const bad = picked.find((f) => !TYPES.includes(f.type));
    if (bad) return setErr(`${bad.name}: yalnız JPEG, PNG ya da WebP resim eklenebilir`);
    const big = picked.find((f) => f.size > MAX_MB * 1024 * 1024);
    if (big) return setErr(`${big.name}: en fazla ${MAX_MB} MB olabilir`);
    const next = [...files, ...picked];
    if (next.length > MAX_SHOTS) setErr(`En fazla ${MAX_SHOTS} ekran görüntüsü eklenebilir`);
    setFiles(next.slice(0, MAX_SHOTS));
  };

  const submit = async () => {
    if (!message.trim()) return setErr("Ne olduğunu kısaca yaz");
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("message", message.trim());
      fd.append("context", JSON.stringify({
        route: window.location.hash || "#/", platform: "web", book: bookOf(),
        ...(context?.error ? { error: context.error.slice(0, 900) } : {}), ...(context?.code ? { code: context.code } : {}),
      }));
      for (const f of files) fd.append("screenshots", f, f.name);
      await call("POST", "/api/feedback", fd);
      setDone(true);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Modal title="Gönderildi" onClose={onClose}>
        <div className="space-y-2 py-4 text-center">
          <CheckCircle2 size={40} className="mx-auto text-green-600" aria-hidden />
          <p className="font-medium">Teşekkürler, mesajın bize ulaştı.</p>
          <p className="text-sm text-stone-500">İnceleyip düzelteceğiz.</p>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <div className="flex-1" />
      <button type="button" className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={busy || !message.trim()} onClick={() => void submit()}>{busy ? "Gönderiliyor…" : "Gönder"}</button>
    </>}>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Ne oldu, ne bekliyordun? Ekran görüntüsü eklersen sorunu daha hızlı buluruz. Mesajın yalnız yöneticiye gider.
        </p>
        {context?.error && (
          <p className="rounded-lg bg-stone-100 p-2 text-xs break-words text-stone-600 dark:bg-stone-800 dark:text-stone-300">Hata: {context.error}</p>
        )}
        <div>
          <label className="label" htmlFor="fb-msg">Mesaj</label>
          <textarea id="fb-msg" className="input min-h-28" maxLength={MAX_MESSAGE} autoFocus value={message}
            placeholder="ör. Fiş okuttuktan sonra tutar boş geldi" onChange={(e) => setMessage(e.target.value)} />
          <div className="mt-0.5 text-right text-xs text-stone-500 tabular-nums">{message.length}/{MAX_MESSAGE}</div>
        </div>
        <div>
          <div className="label">Ekran görüntüsü (isteğe bağlı, en çok {MAX_SHOTS})</div>
          <div className="flex flex-wrap gap-2">
            {files.map((f, i) => (
              <div key={`${f.name}-${i}`} className="relative">
                <img src={urls[i]} alt={`Ekran görüntüsü ${i + 1}`} className="size-20 rounded-lg border border-stone-200 object-cover dark:border-stone-700" />
                <button type="button" aria-label={`${i + 1}. görüntüyü çıkar`} onClick={() => setFiles(files.filter((_, j) => j !== i))}
                  className="absolute -top-2 -right-2 flex size-6 items-center justify-center rounded-full bg-stone-800 text-white shadow dark:bg-stone-200 dark:text-stone-900">
                  <X size={14} aria-hidden />
                </button>
              </div>
            ))}
            {files.length < MAX_SHOTS && (
              <button type="button" onClick={() => input.current?.click()}
                className="flex size-20 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-stone-300 text-xs text-stone-500 hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/50">
                <ImagePlus size={20} aria-hidden /> Ekle
              </button>
            )}
          </div>
          <input ref={input} type="file" accept={TYPES.join(",")} multiple className="hidden"
            onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          <p className="mt-1 text-xs text-stone-500">JPEG, PNG ya da WebP; her biri en çok {MAX_MB} MB. Tutarlar görünüyorsa göz simgesiyle gizleyebilirsin.</p>
        </div>
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}

/** "Sorun bildir" düğmesi + penceresi (menü, giriş ekranı). */
export function FeedbackButton({ className, label = "Sorun bildir", icon = true }: { className?: string; label?: string; icon?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        {icon && <MessageSquareWarning size={16} aria-hidden />}{label}
      </button>
      {open && <FeedbackDialog onClose={() => setOpen(false)} />}
    </>
  );
}

const ISSUES = "https://github.com/alameddinc/hesapkitap/issues/new";

/** Hata ekranındaki "Sorun bildir" (fiş / ekstre okunamadı): hata metni ve kodu bağlama eklenir (konu: hatanın
 *  görüldüğü yer, metnin önüne). Sunucu "feedback" bilmiyorsa GitHub'da konu açılır ("Bize bildir"). */
export function ReportProblem({ subject, error, code, className }: { subject: string; error?: string | null; code: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const on = !!useMeta().data?.capabilities?.includes("feedback");
  const cls = className ?? "inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline dark:text-blue-400";
  if (!on) return (
    <a className={cls} target="_blank" rel="noreferrer"
      href={`${ISSUES}?title=${encodeURIComponent(subject)}&body=${encodeURIComponent(`Hata: ${error ?? "?"} (${code})\nİstemci: web`)}`}>
      <MessageSquareWarning size={14} aria-hidden />Bize bildir</a>
  );
  return (
    <>
      <button type="button" className={cls} onClick={() => setOpen(true)}><MessageSquareWarning size={14} aria-hidden />Sorun bildir</button>
      {open && <FeedbackDialog onClose={() => setOpen(false)} context={{ error: [subject, error].filter(Boolean).join(": "), code }} />}
    </>
  );
}
