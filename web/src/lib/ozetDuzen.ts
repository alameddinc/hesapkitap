// Özet düzeni (PUT /api/me/ui layout): sonradan eklenen bileşen, kayıtlı düzeni olan kullanıcıda da görünsün; kaldırınca
// bir daha kendiliğinden gelmesin. Kaldırılan yeni bileşen düzende "-ad" (işaret) olarak saklanır; istemciler bilmediği
// adı zaten atar (WIDGETS süzgeci), sunucu listeyi olduğu gibi saklar.
//
// Özet görünümü (kullanıcıya görünen adlar Minimal / Ayrıntılı; Sade görünüm ayrı bir özellik, ui.simple): düzen "sade"
// (Tek bakış: Harcanabilir + 3 satır) ile başlıyorsa Minimal; "ayrinti" bir ayraçtır, ardındakiler kapalı "Ayrıntılar"
// bölümünde. Kaydı yoksa Minimal (varsayılan). Görünüm değişince öbürünün sırası düzende "=ad" öğeleriyle saklanır
// (gorunumDegistir); eski "-sade" işareti (kaldırılan öneri kartı) yok sayılır. Mobil lib/endpoints/islem.ts ile aynı kural.

/** Sonradan eklenen bileşenler → kayıtlı düzende hangisinin ardına girer. "sade" / "ayrinti" burada yok: eski düzene
 *  kendiliğinden girmez. */
export const YENI_BILESEN: Record<string, string> = { "donem-sonu": "kartlar", "faiz-ucret": "kartlar", sadaka: "uyarilar" };

const isaret = (id: string) => `-${id}`;
const SAKLI = "=";
export type Gorunum = "minimal" | "ayrintili";
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

/** Kayıttaki öbür görünümün saklı düzeni ("=ad" öğeleri, işaretleriyle; yoksa boş). */
export const sakliDuzen = (saved: string[] | null | undefined): string[] =>
  (saved ?? []).filter((id) => id.startsWith(SAKLI)).map((id) => id.slice(SAKLI.length));

/** Görünümü değiştir: hedefin saklı sırası (yoksa o görünümün varsayılanı) gelir; şimdiki sıra (işaretleriyle) saklanır. */
export function gorunumDegistir(layout: string[], sakli: string[], hedef: Gorunum, known: (id: string) => boolean,
  varsayilan: { minimal: string[]; ayrintili: string[] }): { layout: string[]; sakli: string[] } {
  const minimal = hedef === "minimal";
  if (minimal === sadeMi(layout)) return { layout, sakli };
  const geri = sakli.length ? ozetDuzeni(sakli, known, varsayilan[hedef]) : [];
  const uygun = geri.length > 0 && sadeMi(geri) === minimal;
  return { layout: uygun ? geri : varsayilan[hedef].filter(known), sakli: kayitDuzeni(layout) };
}

/** Kaydedilecek düzen: görünen sıra + düzende olmayan yeni bileşenlerin işareti (kaldırıldı) + öbür görünümün saklı
 *  sırası ("=ad"). */
export const kayitDuzeni = (layout: string[], sakli: string[] = []) =>
  [...layout, ...Object.keys(YENI_BILESEN).filter((id) => !layout.includes(id)).map(isaret), ...sakli.map((id) => SAKLI + id)];
