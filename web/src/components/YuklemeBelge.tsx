// Toplu yüklemede okunan fiş/fatura (lib/yuklemeBelge.ts): Ekstreler satırı ve Özet → Yapılacaklar ortak parçaları.
// Onayla: fatura → fatura onay formu (Ödemelere ekle), fiş → gider formu; ikisi de okunmuş belgeyle dolu açılır (yeniden
// okunmaz, hak sunucuda düştü). Tutarlar tl() ile (göz kapalıysa ••••).
import { useSyncExternalStore } from "react";
import type { UploadResult } from "../api";
import { dateTR, tl } from "../format";
import { belgeAbone, belgeOzet, bekleyenBelgeler, faturaTaslak, isFatura } from "../lib/yuklemeBelge";
import { useUI } from "../ui";
import { docToDraft } from "./AddSheet";

export const useBekleyenBelgeler = () => useSyncExternalStore(belgeAbone, bekleyenBelgeler);

/** Okunan belgenin onay formunu aç. */
export function useBelgeAc() {
  const ui = useUI();
  return (r: UploadResult, file: File | null) => {
    if (!r.doc) return;
    if (isFatura(r)) ui.openBill(faturaTaslak(r));
    else ui.openEntry(undefined, docToDraft(r.doc, file));
  };
}

/** "Örnek Elektrik · 412,50 ₺ · son ödeme 25.11.2026" */
export function BelgeOzet({ r, className }: { r: UploadResult; className?: string }) {
  const o = belgeOzet(r);
  const parts = [o.ad, o.tutar != null ? tl(o.tutar) : null, o.tarih ? `${o.tarihEtiket ? `${o.tarihEtiket} ` : ""}${dateTR(o.tarih)}` : null]
    .filter(Boolean);
  if (!parts.length) return null;
  return <span className={className}>{parts.join(" · ")}</span>;
}
