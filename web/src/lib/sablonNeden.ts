// Kart ekstresi şablonu öğrenme denemelerinin neden kodları (app/card_reader.py explain_from_ai, learn_from_ai;
// app/card_reader_backfill.py). Yönetici panelinde Şablonlar → Kart ekstresi şablonları tablosunda gösterilir.
export const NEDEN: Record<string, string> = {
  learned: "öğrenildi",
  revived: "yeniden açıldı",
  known: "zaten vardı",
  would_learn: "öğrenilecek",
  already_local: "zaten yapay zekâsız okunuyor",
  no_text: "metin yok (taranmış PDF)",
  too_long: "metin çok uzun",
  ai_few_txns: "yapay zekâ 2'den az işlem okudu",
  ai_equation: "yapay zekâ okuması denklemi tutmuyor",
  ai_dates: "yapay zekâ tarihleri tutarsız",
  bank_not_in_text: "banka adı metinde yok (logoda)",
  bank_unknown: "banka tanınmadı",
  bank_mismatch: "metindeki banka farklı",
  date_format: "işlem satırı tarih biçimi bulunamadı",
  header: "kesim tarihi / dönem borcu bulunamadı",
  header_values: "başlık değerleri farklı",
  lines: "işlem satırı sayısı farklı",
  amounts: "tutarlar farklı",
  card: "kart / ek kart farklı",
  equation: "ekstre denklemi tutmadı",
  dates: "tarihler dönem dışında",
  fields: "asgari / limit farklı",
  no_file: "saklı PDF yok",
  password: "PDF şifreli",
  unreadable: "PDF açılamadı",
  error: "beklenmeyen hata",
};

/** Kod → Türkçe açıklama; bilinmeyen kod olduğu gibi. */
export const nedenMetni = (kod: string) => NEDEN[kod] ?? kod;

/** Neden kodları sayıya göre azalan: [[kod, sayı], …] — başarılılar (öğrenildi vb.) sona. */
export function nedenSirasi(reasons: Record<string, number>): [string, number][] {
  const iyi = new Set(["learned", "revived", "known", "would_learn", "already_local"]);
  return Object.entries(reasons).sort((a, b) => Number(iyi.has(a[0])) - Number(iyi.has(b[0])) || b[1] - a[1] || a[0].localeCompare(b[0]));
}
