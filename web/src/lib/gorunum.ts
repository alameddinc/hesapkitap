// Özet görünümü: Minimal (tek rakam + 3 satır, gerisi Ayrıntılar'da; varsayılan), Ayrıntılı (bütün bölümler açık; klasik),
// Sade (büyük yazı, daha az ayrıntı; ui.simple, SimpleDashboard). Seçim sunucuda (meta.ui), cihazlar arasında aynı.
// Ölçüm yalnız toplam (POST /api/events, sunucu yeteneği ozet_gorunum): hangi görünümde açıldı, elle değişti. Mobil
// src/lib/gorunum.ts ile aynı metinler.
import { olcumGonder } from "./olcum.ts";
import { sadeMi } from "./ozetDuzen.ts";

export type OzetGorunum = "minimal" | "ayrintili" | "sade";
export const GORUNUMLER: OzetGorunum[] = ["minimal", "ayrintili", "sade"];
export const GORUNUM_ADI: Record<OzetGorunum, string> = { minimal: "Minimal", ayrintili: "Ayrıntılı", sade: "Sade" };
export const GORUNUM_ACIKLAMA: Record<OzetGorunum, string> = {
  minimal: "Tek rakam ve en önemli 3 satır; gerisi Ayrıntılar'da",
  ayrintili: "Bütün bölümler açık",
  sade: "Büyük yazı, daha az ayrıntı",
};

/** Şu anki görünüm: Sade açıksa Sade; görünen düzen Tek bakış'la başlıyorsa Minimal. */
export const simdikiGorunum = (simple: boolean, layout: string[]): OzetGorunum => (simple ? "sade" : sadeMi(layout) ? "minimal" : "ayrintili");

const gonderildi = new Set<string>();
/** Özet (ya da Sade görünüm) açıldı: görünümün günlük toplamı; oturumda bir kez. */
export function gorunumAcildi(g: OzetGorunum, caps: string[] | undefined): void {
  if (!caps?.includes("ozet_gorunum") || gonderildi.has(g)) return;
  gonderildi.add(g);
  olcumGonder(`ozet_gorunum_${g}`, caps);
}
/** Görünüm elle değişti (nereden → nereye). */
export function gorunumDegisti(from: OzetGorunum, to: OzetGorunum, caps: string[] | undefined): void {
  if (from === to || !caps?.includes("ozet_gorunum")) return;
  olcumGonder(`ozet_degisti_${from}_${to}`, caps);
}
