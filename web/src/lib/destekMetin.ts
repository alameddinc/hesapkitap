// Destek metinleri ve bağlam maskeleme (saf; lib/destek.ts yeniden dışa verir, testler doğrudan bunu alır).
export const CODES: Record<string, string> = {
  ekstre_bulunamadi: "Kart ekstresi bulunamadı",
  okunamadi: "Okuyucu belgeyi çözemedi",
  sinir: "Sayfa / süre sınırı",
  metin_yok: "PDF'te metin yok (taranmış)",
};
export const MESAJ_EN_COK = 2000;
export const GORSEL_EN_COK = 3;
export const BAGLAM_EN_COK = 300;
export const BEKLEME_GUN = 30;

export interface YardimBaglami { nerede: string; kod?: string | number | null; ayrinti?: string | null }
export interface ReportSummary { code?: string; code_label?: string; file_type?: string; pages?: number | null; bank_guess?: string; bank_name?: string | null; sent_doc?: boolean }

/** Bağlamı kişisel veriden arındır: e-posta, 6+ haneli rakam dizileri (kart, hesap, TC, telefon); en çok 300 karakter. */
export function maskeleBaglam(s: string | null | undefined): string {
  return (s ?? "")
    .replace(/\S+@\S+/g, "[e-posta]")
    .replace(/\d[\d\s-]{4,}\d/g, (m) => (m.replace(/\D/g, "").length >= 6 ? "[numara]" : m))
    .replace(/\s+/g, " ").trim().slice(0, BAGLAM_EN_COK);
}

/** Dosya satırı: "PDF · 3 sayfa". */
export function dosyaMetni(r: { file_type?: string; pages?: number | null }): string {
  return [r.file_type ? r.file_type.toUpperCase() : null, r.pages ? `${r.pages} sayfa` : null].filter(Boolean).join(" · ") || "Bilinmiyor";
}

/** Ekstre bildirimi özeti: "Ornek Bank · Kart ekstresi bulunamadı · PDF · 1 sayfa". */
export function bildirimOzeti(r: ReportSummary): string {
  return [r.bank_name || r.bank_guess || null, r.code_label || (r.code ? CODES[r.code] : null),
    r.file_type ? r.file_type.toUpperCase() : null, r.pages ? `${r.pages} sayfa` : null].filter(Boolean).join(" · ");
}

/** Bağlam çipinin metni: "Bağlam: Ekstre yükleme · kod sinir". */
export const baglamMetni = (b: YardimBaglami) => `Bağlam: ${[b.nerede, b.kod != null && b.kod !== "" ? `kod ${b.kod}` : null].filter(Boolean).join(" · ")}`;
