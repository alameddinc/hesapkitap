// Kaydı hangi deftere yazalım (Aile / ortak defter, sunucu yeteneği "save_book_choice"; mobil ile aynı metinler):
// ortak defter üyesi fişi, faturayı ya da elle kaydı açık defterden bağımsız "Ortak" ya da "Benim" defterine kaydeder.
// İstek X-HK-Book: kendi | ortak başlığıyla gider (yalnız açık defterden farklıysa); sunucu saklı fotoğrafı/dosyayı
// üyenin iki defteri arasında kopyalar. Ortak deftere yazarken gövdede isteğe bağlı spender: "u<id>" | "ortak".
// Saf yardımcılar: React ve api.ts'e bağlı değil (node --test).

export type BookKey = "kendi" | "ortak";

export interface BookLike {
  member?: boolean;
  book?: BookKey;
  can_switch?: boolean;
  role?: string;
  me?: number | null;
  people?: { id: number }[];
}

/** Üyenin açık defteri; üye değilse (sahip ya da tek kişi) null: seçim yok. */
export const openBookOf = (b?: BookLike | null): BookKey | null =>
  b?.member ? (b.book === "kendi" ? "kendi" : "ortak") : null;

/** "Deftere" seçimi görünsün mü: sunucu yeteneği var ve kullanıcı ortak defter üyesi (defter değiştirebiliyor). */
export const canChooseBook = (caps: readonly string[] | undefined, b?: BookLike | null): boolean =>
  !!caps?.includes("save_book_choice") && !!b?.member && b.can_switch !== false;

/** İstekte gönderilecek defter: hedef açık defterden farklıysa hedef, değilse null (başlık gönderilmez). */
export const bookOverride = (target: BookKey | null | undefined, open: BookKey | null): BookKey | null =>
  target && open && target !== open ? target : null;

/** X-HK-Book başlığı (bookOverride sonucu): null → boş. */
export const bookHeaders = (book: BookKey | null | undefined): Record<string, string> => (book ? { "X-HK-Book": book } : {});

/** Kayıt diğer deftere gittiyse bildirimi ("Aç" ile o deftere geçilir); aynı defterse null. */
export const savedElsewhere = (target: BookKey | null | undefined, open: BookKey | null): string | null => {
  const o = bookOverride(target, open);
  return o === "kendi" ? "Benim defterine kaydedildi" : o === "ortak" ? "Ortak deftere kaydedildi" : null;
};

/** Seçimin altındaki açıklama. */
export const bookHint = (target: BookKey): string =>
  target === "kendi" ? "Yalnız sen görürsün." : "Ailedeki herkes görür (yetkisine göre).";

/** Hedef ortak defter mi: üyede seçilen defter; sahipte (üye değil) kendi defteri zaten ortak defterdir. */
export const targetIsShared = (target: BookKey | null, b?: BookLike | null): boolean =>
  b?.member ? target === "ortak" : (b?.people?.length ?? 0) > 1;

/** Ortak defterde "Sadece görür": hedef ortaksa kaydedemez (Benim'e kaydedebilir). */
export const targetViewer = (target: BookKey | null, b?: BookLike | null): boolean =>
  !!b?.member && b.role === "viewer" && target === "ortak";

/** "Kim harcadı" çiplerinin sırası: Sen, diğer kişiler, Ortak. */
export const spenderKeys = (people: { id: number }[], me: number | null | undefined): string[] => {
  const mine = people.filter((p) => p.id === me);
  return [...mine, ...people.filter((p) => p.id !== me)].map((p) => `u${p.id}`).concat("ortak");
};

/** Kayıt gövdesine eklenecek harcayan: seçilmediyse ya da "Sen" ise hiçbir şey (sunucu ekleyene yazar). */
export const spenderField = (choice: string | null | undefined, me: number | null | undefined): { spender?: string } =>
  !choice || choice === `u${me}` ? {} : { spender: choice };

/** Günlük okuma durumunda Aile ortak hakkı (sunucu isteğe bağlı family_left / family_limit): kişinin kalanından
 *  azsa "Aile bugün 12/120 kaldı", değilse null. */
export function familyReadsText(r: { left?: number; family_left?: number | null; family_limit?: number | null } | null | undefined): string | null {
  if (!r || typeof r.family_left !== "number" || typeof r.family_limit !== "number") return null;
  if (typeof r.left === "number" && r.family_left >= r.left) return null;
  return `Aile bugün ${Math.max(0, r.family_left)}/${r.family_limit} kaldı`;
}

/** Okuma yanıtında cached: true → aynı belge daha önce okunmuştu, hak düşmedi. */
export const CACHED_NOTE = "Bu belge daha önce okunmuştu; hak düşmedi.";
