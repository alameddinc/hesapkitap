// "Yardım al" (destek sohbeti) — İNCE ARAYÜZ. Gerçek uygulama başka bir dalda (destek sohbeti; feat/bildir-yanit):
// birleştirmede bu dosya o uygulamayla değişecek. Ad ve imza AYNEN kalmalı (mobil src/lib/destek.ts ile aynı):
//   yardimAl(baglam: { nerede: string; kod?: string; ayrinti?: string }): void
// Şimdilik "Sorun bildir" penceresini (components/Feedback.tsx) bağlamla açar: App'teki <DestekKapisi/> (components/
// DestekKapisi.tsx) bu küçük durumu dinler. nerede + ayrinti → context.error, kod → context.code (kullanıcıya gösterilmez;
// yalnız yöneticiye giden bağlam).
import { useSyncExternalStore } from "react";

export interface DestekBaglam {
  /** hatanın görüldüğü yer (ekran adı: "Ekstreler", "Fiş okuma") */
  nerede: string;
  /** kısa sabit kod (lib/hataAcikla.ts: zaman_asimi, sunucu_500, okuma_hakki…) */
  kod?: string;
  /** teknik ayrıntı (HTTP durumu, hata adı); kullanıcıya gösterilmez */
  ayrinti?: string;
}

let acik: (DestekBaglam & { no: number }) | null = null;
let no = 0;
const dinleyen = new Set<() => void>();
const yay = () => dinleyen.forEach((f) => f());

export function yardimAl(baglam: DestekBaglam): void {
  acik = { ...baglam, no: ++no };
  yay();
}

/** Açık "Yardım al" bağlamı (DestekKapisi çizer); kapatınca null. */
export const useDestek = () => useSyncExternalStore((f) => (dinleyen.add(f), () => dinleyen.delete(f)), () => acik);
export const destekKapat = () => { acik = null; yay(); };
