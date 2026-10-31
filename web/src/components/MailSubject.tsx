import { Copy } from "lucide-react";
import { useUI } from "../ui";

/** Mail konusu: hiç kesilmez, seçilip kopyalanabilir. Gmail yönlendirme onayı gibi konuda kod varsa (6+ haneli sayı)
 *  yanında "Kodu kopyala" düğmesi. */
export default function MailSubject({ subject, className }: { subject: string | null; className?: string }) {
  const ui = useUI();
  const code = subject?.match(/\d{6,}/)?.[0];
  const copy = (text: string) =>
    navigator.clipboard.writeText(text).then(() => ui.toast(`Kod kopyalandı: ${text}`), () => ui.error("Kopyalanamadı; konuyu elle seç"));
  return (
    <span className={className}>
      <span className="break-words select-text">{subject || "(konusuz)"}</span>
      {code && (
        <button type="button" className="btn-ghost ml-1 inline-flex px-1.5 py-0.5 align-middle text-xs text-blue-600 dark:text-blue-400"
          aria-label={`Kodu kopyala: ${code}`} onClick={() => void copy(code)}>
          <Copy size={12} aria-hidden /> Kodu kopyala
        </button>
      )}
    </span>
  );
}
