// (i): bir kavramın kısa açıklaması (lib/terimler.ts). Etiketin yanında küçük ikon; dokunma alanı büyük ama yerleşimi
// büyütmez (eksi kenar boşluğu). Dokununca pencerede açıklama (title değil: dokunmatik ekranda da görünür).
// Üst öğe <button> olmamalı (iç içe düğme olmaz): kart tıklanıyorsa kartın düğmesi kardeş öğe olarak gerilir.
import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../format";
import { TERIM, type TerimId } from "../lib/terimler";
import Modal from "./Modal";

/** Sunucu tanımı "Kalan: gelir − gider." biçiminde gelir; başlık zaten adı gösterdiği için baştaki "Ad:" atılır. */
const body = (s: string) => {
  const t = s.trim().replace(/^[^:.]{1,40}:\s*/, "");
  return t.charAt(0).toLocaleUpperCase("tr") + t.slice(1);
};

export default function TerimBilgi({ id, metin, ek, className }: {
  id: TerimId;
  /** sunucunun tanım metni (summary.definitions, net-worth.spendable_explain): varsa sözlüğün metni yerine bu */
  metin?: string | null;
  /** açıklamanın altına (ör. bugünkü rakamla cümle) */
  ek?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const t = TERIM[id];
  return (
    <>
      <button type="button" aria-label={`${t.ad} nedir?`} aria-haspopup="dialog"
        className={cx("pointer-events-auto relative z-10 -m-1.5 inline-flex rounded-full p-1.5 align-middle opacity-70 hover:opacity-100", className)}
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
        <Info size={13} aria-hidden />
      </button>
      {open && (
        <Modal title={t.ad} onClose={() => setOpen(false)}
          footer={<><div className="flex-1" /><button className="btn-primary" onClick={() => setOpen(false)}>Tamam</button></>}>
          <div className="space-y-2 text-sm text-stone-700 dark:text-stone-300" onClick={(e) => e.stopPropagation()} role="presentation">
            <p>{metin?.trim() ? body(metin) : t.aciklama}</p>
            {ek}
          </div>
        </Modal>
      )}
    </>
  );
}
