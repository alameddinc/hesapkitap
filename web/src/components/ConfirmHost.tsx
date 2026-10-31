import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx } from "../format";
import { onAsk, type AskRequest } from "../lib/ask";
import Modal from "./Modal";

/** ask() isteklerini çizer: başlık, açıklama, tehlikeli eylemde kırmızı düğme ve "Geri alınamaz" satırı. */
export default function ConfirmHost() {
  const [req, setReq] = useState<AskRequest | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { onAsk(setReq); return () => onAsk(null); }, []);
  useEffect(() => { if (req) cancelRef.current?.focus(); }, [req]);
  if (!req) return null;
  return (
    <Modal title={req.title} onClose={() => req.resolve(false)} footer={<>
      <div className="flex-1" />
      <button ref={cancelRef} className="btn-ghost" onClick={() => req.resolve(false)}>{req.cancel}</button>
      <button className={cx("btn text-white", req.danger ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700")}
        onClick={() => req.resolve(true)}>{req.confirm}</button>
    </>}>
      <div className="space-y-2 text-sm">
        {req.body && <p className="whitespace-pre-line text-stone-700 dark:text-stone-300">{req.body}</p>}
        {req.irreversible && (
          <p className="flex items-center gap-1.5 font-medium text-red-700 dark:text-red-400">
            <AlertTriangle size={14} aria-hidden /> Geri alınamaz.</p>
        )}
        {!req.body && !req.irreversible && <p className="text-stone-500 dark:text-stone-400">Onaylıyor musun?</p>}
      </div>
    </Modal>
  );
}
