import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { cx } from "../format";
import { useUI } from "../ui";

export interface ToastState { msg: string; kind: "ok" | "error"; undo?: () => Promise<unknown>; id?: number }

/** Okuma süresi: kelime başına 0,3 sn, en az 3 sn; geri alınabilir işlemde en az 8 sn (düğmeye yetişilsin). */
export const toastMs = (msg: string, undo: boolean) =>
  Math.max(undo ? 8000 : 3000, msg.trim().split(/\s+/).length * 300);

/** Alt bildirim. Başarı: koyu şerit, role=status, süreyle kapanır (fare/odak üstündeyken bekler), isteğe bağlı "Geri al".
 *  Hata: kırmızı kutu, role=alert, kendiliğinden kapanmaz (WCAG 2.2.1); kapatma düğmesi. */
export default function Toast({ t, onClose }: { t: ToastState; onClose: () => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const [hold, setHold] = useState(false);
  const [busy, setBusy] = useState(false);
  const error = t.kind === "error";
  useEffect(() => {
    if (error || hold || busy) return;
    const h = setTimeout(onClose, toastMs(t.msg, !!t.undo));
    return () => clearTimeout(h);
  }, [t, error, hold, busy, onClose]);
  const undo = async () => {
    if (!t.undo) return;
    setBusy(true);
    try {
      await t.undo();
      void qc.invalidateQueries();
      ui.toast("Geri alındı");
    } catch (e) {
      ui.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(6rem+var(--sab))] z-[60] flex justify-center px-4 md:bottom-6">
      <div role={error ? "alert" : "status"} aria-live={error ? "assertive" : "polite"}
        onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)} onFocus={() => setHold(true)} onBlur={() => setHold(false)}
        className={cx("pointer-events-auto flex max-w-md items-start gap-2 rounded-xl px-3 py-2 text-sm shadow-lg",
          error ? "border border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100"
            : "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900")}>
        {error ? <AlertCircle size={18} className="mt-px shrink-0 text-red-600 dark:text-red-400" aria-hidden />
          : <CheckCircle2 size={18} className="mt-px shrink-0 text-green-400 dark:text-green-700" aria-hidden />}
        <span className="min-w-0 flex-1 py-px">{t.msg}</span>
        {t.undo && (
          <button className="-my-1 shrink-0 rounded-lg px-2 py-1 font-semibold text-blue-300 hover:bg-white/10 disabled:opacity-60 dark:text-blue-700 dark:hover:bg-black/5"
            disabled={busy} onClick={() => void undo()}>{busy ? "Geri alınıyor…" : "Geri al"}</button>
        )}
        {(error || t.undo) && (
          <button className={cx("-my-1 -mr-1 shrink-0 rounded-lg p-1", error ? "hover:bg-red-100 dark:hover:bg-red-900" : "hover:bg-white/10 dark:hover:bg-black/5")}
            aria-label="Bildirimi kapat" onClick={onClose}><X size={16} /></button>
        )}
      </div>
    </div>
  );
}
