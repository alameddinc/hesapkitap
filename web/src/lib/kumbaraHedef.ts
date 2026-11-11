// Kumbara hedef durumu → ekran metni (sunucu app/jar_goal.py hesaplar; mobil src/lib/kumbaraHedef.ts aynı sözler).
// Rozet: Yolunda / Geride / Önde / Tamamlandı (renk tek başına anlam taşımaz: rozet ve cümle her zaman yazılı).
// Faiz/yatırım ürünü önerisi yok: yalnız "ayda ne kadar ayır".

export type HedefDurum = "tamam" | "yolunda" | "geride" | "onde" | "tarihsiz";
export interface GoalStatus {
  status: HedefDurum; unit: string; left: number; months_left: number | null; needed_monthly: number | null;
  pace: number; pace_source: "hareket" | "plan" | null; raise_by: number | null;
  eta: string | null; eta_months: number | null; months_early: number | null; deadline_passed: boolean;
  /** altın/döviz: TL karşılığı (yaklaşık; fiyat yoksa null) */
  left_try?: number | null; needed_monthly_try?: number | null; raise_by_try?: number | null; pace_try?: number | null;
}
export type Ton = "iyi" | "uyari" | "notr";
export interface HedefMetni { rozet: string | null; ton: Ton; satir: string; ayrinti: string | null }

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

/** Yılın bulunma eki: 2027'de, 2030'da, 2033'te, 2026'da. */
export function yilEki(y: number): string {
  const birler = ["", "de", "de", "te", "te", "te", "da", "de", "de", "da"];
  const onlar = ["", "da", "de", "da", "ta", "de", "ta", "te", "de", "da"];
  if (y % 10) return birler[y % 10]!;
  if (y % 100) return onlar[(y % 100) / 10]!;
  return "de"; // 2100'de (yüz), 2000'de (bin)
}
/** "2027-03" → "Mart 2027'de" */
export const ayda = (ym: string) => { const y = Number(ym.slice(0, 4)); return `${AYLAR[Number(ym.slice(5, 7)) - 1]} ${y}'${yilEki(y)}`; };

/**
 * para: TL tutarı (gizleme dahil, ör. ui.money(tl0(n))); birim: kumbaranın biriminde miktar ("1,5 gr", "₺2.500").
 * TL kumbarasında birim = para.
 */
export function hedefMetni(g: GoalStatus, f: { para: (n: number) => string; birim: (n: number) => string }): HedefMetni {
  const fx = g.unit !== "TRY";
  const fiil = fx ? "al" : "ayır";
  const tlEk = (n?: number | null) => (fx && n ? ` (≈\u00a0${f.para(n)})` : "");
  const hiz = g.pace > 0 ? `ayda ${f.birim(g.pace)}${tlEk(g.pace_try)}` : null;
  const hizCumle = !hiz ? null : g.pace_source === "plan" ? `Planına göre ${hiz} ${fx ? "alacaksın" : "ayıracaksın"}.`
    : `Son aylarda ${hiz} ${fx ? "alıyorsun" : "ayırıyorsun"}.`;
  if (g.status === "tamam") return { rozet: "Tamamlandı", ton: "iyi", satir: "Hedefe ulaştın", ayrinti: null };
  if (g.status === "tarihsiz") {
    return g.eta
      ? { rozet: null, ton: "notr", satir: `Bu hızla ${ayda(g.eta)} dolar`, ayrinti: `${hizCumle} ${f.birim(g.left)}${tlEk(g.left_try)} kaldı.` }
      : { rozet: null, ton: "notr", satir: `${f.birim(g.left)}${tlEk(g.left_try)} kaldı`, ayrinti: `${fx ? "Aldıkça" : "Ayırdıkça"} ne zaman dolacağını gösteririz.` };
  }
  if (g.deadline_passed) {
    return { rozet: "Geride", ton: "uyari", satir: `Son tarih geçti: ${f.birim(g.left)}${tlEk(g.left_try)} kaldı`,
      ayrinti: `Kalanı ${fiil} ya da yeni bir son tarih koy.` };
  }
  const satir = `Hedefe ${g.months_left} ay: ayda ${f.birim(g.needed_monthly ?? 0)}${tlEk(g.needed_monthly_try)} ${fiil}`;
  if (g.status === "geride") {
    return { rozet: "Geride", ton: "uyari", satir, ayrinti: hiz
      ? `Bu hızla (ayda ${f.birim(g.pace)}) yetişmez: ayda ${f.birim(g.raise_by ?? 0)}${tlEk(g.raise_by_try)} daha ${fiil}.`
      : `Son 3 ayda ${fx ? "alım" : "ayırma"} yok; ayda ${f.birim(g.needed_monthly ?? 0)} ${fiil}ırsan yetişir.` };
  }
  if (g.status === "onde") {
    return { rozet: "Önde", ton: "iyi", satir, ayrinti: `${hizCumle} Bu hızla ${ayda(g.eta!)} dolar, ${g.months_early} ay erken.` };
  }
  return { rozet: "Yolunda", ton: "iyi", satir, ayrinti: `${hizCumle} Bu hızla tam zamanında dolar.` };
}
