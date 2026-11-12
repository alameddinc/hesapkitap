// Özet düzeni (PUT /api/me/ui layout): sonradan eklenen bileşen, kayıtlı düzeni olan kullanıcıda da görünsün; kaldırınca
// bir daha kendiliğinden gelmesin. Kaldırılan yeni bileşen düzende "-ad" (işaret) olarak saklanır; istemciler bilmediği
// adı zaten atar (WIDGETS süzgeci), sunucu listeyi olduğu gibi saklar.
//
// Sade Özet: düzen "sade" (Tek bakış: Harcanabilir + 3 satır) ile başlıyorsa sade; "ayrinti" bir ayraçtır, ardındakiler
// kapalı "Ayrıntılar" bölümünde. Kendi düzenini kaydetmiş kullanıcının düzeni zorla sadeye dönmez: bir kez öneri
// (sadeOnerisi) — Uygula: sadeyeGecir; Kalsın: düzende "-sade" işareti. Mobil lib/endpoints/islem.ts ile aynı kural.

/** Sonradan eklenen bileşenler → kayıtlı düzende hangisinin ardına girer. "sade" / "ayrinti" burada yok: eski düzene
 *  kendiliğinden girmez. */
export const YENI_BILESEN: Record<string, string> = { "donem-sonu": "kartlar", "faiz-ucret": "kartlar", sadaka: "uyarilar" };

const isaret = (id: string) => `-${id}`;
/** "Yeni sade Özet'i dene"de Kalsın: öneri bir daha çıkmaz. */
export const SADE_RED = "-sade";
export const sadeMi = (layout: readonly string[]) => layout[0] === "sade";

/** Kayıtlı düzen + yeni bileşenler (kaldırılmadıysa, ardına girdiği bileşenin hemen arkasına; o yoksa başa — sade
 *  düzende Tek bakış'ın ardına). Bilinmeyen adlar (known dışı, işaretler) atılır. Kayıt yoksa varsayılan. Sade düzende
 *  "sade" hep en üstte, "ayrinti" ayracı hep var. */
export function ozetDuzeni(saved: string[] | null | undefined, known: (id: string) => boolean, varsayilan: string[]): string[] {
  if (!saved) return varsayilan.filter(known);
  let out = saved.filter(known);
  const sade = out.includes("sade");
  if (sade) {
    out = ["sade", ...out.filter((id) => id !== "sade")];
    if (!out.includes("ayrinti")) out.splice(1, 0, "ayrinti");
  } else out = out.filter((id) => id !== "ayrinti");
  for (const [id, after] of Object.entries(YENI_BILESEN)) {
    if (!known(id) || out.includes(id) || saved.includes(isaret(id))) continue;
    if (sade && id === "faiz-ucret") continue; // sade düzende faiz Tek bakış'ın 3. satırı: kart kendiliğinden eklenmez
    const i = out.indexOf(after);
    out.splice(i >= 0 ? i + 1 : sade ? out.length : 0, 0, id); // after yoksa başa (sade düzende sona, Ayrıntılar'a)
  }
  return out;
}

/** Kendi (sade olmayan) düzenini kaydetmiş ve öneriyi yanıtlamamış: Özet'te bir kez "Yeni sade Özet'i dene". */
export const sadeOnerisi = (saved: string[] | null | undefined, known: (id: string) => boolean) =>
  !!saved && saved.some(known) && !saved.includes("sade") && !saved.includes(SADE_RED);

/** Öneride Uygula: kullanıcının sırası ve kaldırdıkları Ayrıntılar'ın içinde korunur; faiz kartı Tek bakış'ın 3. satırı
 *  olduğu için düşer. ust: Ayrıntılar'ın üstünde kalacak bileşenler (Başlarken, Telefondan gelenler). */
export function sadeyeGecir(layout: string[], ust: readonly string[] = []): string[] {
  const rest = layout.filter((id) => id !== "sade" && id !== "ayrinti" && id !== "faiz-ucret");
  return ["sade", ...rest.filter((id) => ust.includes(id)), "ayrinti", ...rest.filter((id) => !ust.includes(id))];
}

/** Kaydedilecek düzen: görünen sıra + düzende olmayan yeni bileşenlerin işareti (kaldırıldı). red: sade öneriye Kalsın. */
export const kayitDuzeni = (layout: string[], red = false) =>
  [...layout, ...Object.keys(YENI_BILESEN).filter((id) => !layout.includes(id)).map(isaret), ...(red && !sadeMi(layout) ? [SADE_RED] : [])];
