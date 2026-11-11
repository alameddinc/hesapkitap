// Özet düzeni (PUT /api/me/ui layout): sonradan eklenen bileşen, kayıtlı düzeni olan kullanıcıda da görünsün; kaldırınca
// bir daha kendiliğinden gelmesin. Kaldırılan yeni bileşen düzende "-ad" (işaret) olarak saklanır; istemciler bilmediği
// adı zaten atar (WIDGETS süzgeci), sunucu listeyi olduğu gibi saklar.

/** Sonradan eklenen bileşenler → kayıtlı düzende hangisinin ardına girer. */
export const YENI_BILESEN: Record<string, string> = { "donem-sonu": "kartlar", "faiz-ucret": "kartlar" };

const isaret = (id: string) => `-${id}`;

/** Kayıtlı düzen + yeni bileşenler (kaldırılmadıysa, ardına girdiği bileşenin hemen arkasına; o yoksa başa). Bilinmeyen
 *  adlar (known dışı, işaretler) atılır. Kayıt yoksa varsayılan. */
export function ozetDuzeni(saved: string[] | null | undefined, known: (id: string) => boolean, varsayilan: string[]): string[] {
  if (!saved) return varsayilan.filter(known);
  const out = saved.filter(known);
  for (const [id, after] of Object.entries(YENI_BILESEN)) {
    if (!known(id) || out.includes(id) || saved.includes(isaret(id))) continue;
    out.splice(out.indexOf(after) + 1, 0, id); // after yoksa indexOf −1 → başa
  }
  return out;
}

/** Kaydedilecek düzen: görünen sıra + düzende olmayan yeni bileşenlerin işareti (kaldırıldı). */
export const kayitDuzeni = (layout: string[]) =>
  [...layout, ...Object.keys(YENI_BILESEN).filter((id) => !layout.includes(id)).map(isaret)];
