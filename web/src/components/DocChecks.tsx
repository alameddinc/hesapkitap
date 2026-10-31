import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, FileText } from "lucide-react";
import { api, type BankDocument } from "../api";
import { dateTR } from "../format";
import { ask } from "../lib/ask";
import { TERIM } from "../lib/terimler";
import { useUI } from "../ui";
import { useSave } from "./hooks";

/** S6: bakiye zinciri tutmayan (verification: dogrulanamadi) yüklenmiş hesap dökümleri. Kullanıcı belgeyle karşılaştırıp
 *  "Rakamlar doğru" der → onaylandi; Yapılacaklar'daki madde düşer. Eski sunucuda alan yok: hiçbir şey çizilmez. */
export default function DocChecks() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["bank-documents"], queryFn: api.bankDocuments, retry: false });
  const confirm = useSave((id: number) => api.confirmBankDocument(id), () => ui.toast(`${TERIM.onaylandi.ad} · bu döküm bir daha sorulmaz`));
  const list = (q.data ?? []).filter((d) => d.verification === "dogrulanamadi");
  if (!list.length) return null;
  const name = (d: BankDocument) => `${d.bank || "Hesap"} dökümü${d.filename ? ` (${d.filename})` : ""}`;
  return (
    <section className="card scroll-mt-16 border-amber-300 p-4 dark:border-amber-800" id="dokum-kontrol">
      <h2 className="flex items-center gap-2 font-semibold"><AlertTriangle size={18} className="text-amber-600" aria-hidden /> Kontrol bekleyen dökümler</h2>
      <p className="mt-0.5 text-xs text-stone-500">
        {TERIM.rakamlarTutmadi.ad}: bu dökümlerde bir satırın bakiyesi öncekiyle uyuşmadı; bir hareket eksik ya da yanlış okunmuş
        olabilir. Belgeyi açıp hareketlerle karşılaştır; doğruysa onayla.
      </p>
      <ul className="mt-2 divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {list.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1 break-words">{name(d)}
              <span className="block text-xs text-stone-500">{dateTR(d.uploaded_at.slice(0, 10))} yüklendi</span></span>
            {!!d.stored && (
              <a className="btn-ghost border border-stone-300 px-2.5 py-1 text-xs dark:border-stone-700" href={`/api/bank-import/documents/${d.id}/file`}
                target="_blank" rel="noreferrer"><FileText size={14} aria-hidden /> Belgeyi aç</a>
            )}
            <button className="btn-primary px-2.5 py-1 text-xs" disabled={confirm.isPending}
              onClick={() => void ask(`${name(d)} doğru mu?\n\nBelgeyle karşılaştırdıysan ve hareketler doğruysa onayla; bir daha sorulmaz.`,
                { confirm: "Rakamlar doğru", danger: false }).then((ok) => { if (ok) confirm.mutate(d.id); })}>
              <Check size={14} aria-hidden /> Rakamlar doğru</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
