// "Alsam mı?" (mobil) / "Alırsam ne olur?" (web) sözleri. İki depoda aynı dosya: hesapkitap-mobile src/lib/alsamMi.ts.
// Sahibin ilkesi: uygulama faize yönlendirmez. Bu yüzden:
// - varsayılan seçim peşin (tek çekim); taksit kullanıcının kendi seçimi
// - vade farkı adıyla ve tutarıyla yazılır: "Vade farkı: ₺X (faiz)"; yumuşak "+₺X fark" yok
// - vade farklı seçeneğe onay (✓ "eksiye düşmüyorsun") verilmez: sonuç nötr, yanında biriktirip peşin alma yolu
// - taksitte (vade farksız da olsa) "Biriktirerek al" satırı: aynı aylık tutarı biriktirirsen peşin alırsın

/** Ekran açılınca seçili taksit sayısı: peşin. */
export const VARSAYILAN_TAKSIT = 1;
/** Seçenekler (peşin önce). */
export const TAKSIT_SECENEK: [number, string][] = [[1, "Peşin"], [3, "3"], [6, "6"], [9, "9"], [12, "12"]];
/** Kilitli sayfanın açıklaması (paket tablosu: app/plans.py "alsam_mi" ile aynı anlam). */
export const ALSAM_KILIT_METNI = "Bir şeyi almadan önce önümüzdeki aylarına etkisini gösterir; taksitte vade farkı varsa ne kadar fazla " +
  "ödeyeceğini söyler, biriktirmek için kumbara açar.";

export type Ton = "iyi" | "notr" | "uyari" | "kotu";
export interface AlsamGirdi {
  amount: number; n: number; extra: number; monthly: number; negative: string[]; tight: string[];
}
export interface AlsamSoz {
  /** toplamın altındaki satır */
  maliyet: { t: string; ton: Ton };
  /** vade farkı varsa: faiz olduğunu ve nasıl önleneceğini söyler */
  faiz: string | null;
  /** bütçe sonucu (vade farklıda ✓ yok) */
  sonuc: { t: string; ton: Ton };
  /** taksitte: biriktirip peşin alma yolu */
  biriktir: string | null;
  /** "Biriktir: kumbara aç" satırının alt yazısı */
  biriktirAlt: string;
}

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const LOC = ["ta", "ta", "ta", "da", "ta", "da", "da", "ta", "de", "de", "da", "ta"];
/** "2026-12" → "Aralık’ta" */
export const ayDaTakvim = (ym: string) => {
  const i = Number(ym.slice(5, 7)) - 1;
  return `${AYLAR[i] ?? ym}’${LOC[i] ?? "da"}`;
};

const KURUS = 0.005;

/** Sunucunun simülasyon sonucundan ekrandaki sözler. para: ₺ biçimi (gizli modda maskeli), ayDa: "Aralık’ta". */
export function alsamSoz(g: AlsamGirdi, para: (x: number) => string, ayDa: (ym: string) => string = ayDaTakvim, ad = ""): AlsamSoz {
  const taksit = g.n > 1;
  const faizli = taksit && g.extra > KURUS;
  const maliyet: AlsamSoz["maliyet"] = !taksit ? { t: "Peşin · vade farkı yok", ton: "iyi" }
    : faizli ? { t: `Vade farkı: ${para(g.extra)} (faiz) · ayda ${para(g.monthly)}`, ton: "uyari" }
      : { t: `Vade farkı yok · ayda ${para(g.monthly)}`, ton: "iyi" };
  const faiz = faizli
    ? `Vade farkı faizdir: ${para(g.extra)} fazladan ödersin. Peşin alır ya da biriktirip alırsan bu tutarı ödemezsin.`
    : null;
  const neg = g.negative[0];
  const tight = g.tight[0];
  const sonuc: AlsamSoz["sonuc"] = neg ? { t: `⚠ ${ayDa(neg)} paran eksiye düşer.`, ton: "kotu" }
    : tight ? { t: `◷ Kart tahminiyle ${ayDa(tight)} sıkışabilirsin.`, ton: "uyari" }
      : faizli ? { t: `Hiçbir ay eksiye düşmüyor; ama ${para(g.extra)} vade farkı (faiz) ödersin.`, ton: "notr" }
        : { t: "✓ Hiçbir ay eksiye düşmüyorsun.", ton: "iyi" };
  const aylik = Math.ceil((g.amount / Math.max(1, g.n)) * 100) / 100;
  const biriktir = taksit && g.amount > 0
    ? `Biriktirerek al: ${g.n} ayda ${para(g.amount)} biriktirirsen (ayda ${para(aylik)}) peşin alırsın` +
      (faizli ? ` ve ${para(g.extra)} vade farkı ödemezsin.` : ".")
    : null;
  const biriktirAlt = taksit && g.amount > 0 ? `Ayda ${para(aylik)} · ${g.n} ayda peşin alırsın`
    : ad ? `“${ad}” için ${para(g.amount)} hedef` : "Ürün adıyla, bu tutar hedef olur";
  return { maliyet, faiz, sonuc, biriktir, biriktirAlt };
}
