// "Şablonla yeniden oku" (yetenek statement_reread): yapay zekânın okuduğu ekstrede satır eksik / fazla / işareti ters,
// aynı PDF şablonla yapay zekâsız okunabiliyor. Cümleler mobille BİREBİR aynı (hesapkitap-mobile src/lib/yenidenOku.ts);
// fark satırlarının metni (label, text, note) sunucudan gelir.
import { tl } from "../format.ts";

export const OKU = {
  baslik: "Ekstre yeniden okunabilir",
  satir: (n: number) => `${n} satır farklı`,
  farkiGor: "Farkı gör",
  uygula: "Uygula",
  simdiDegil: "Şimdi değil",
  aciklama: "Bu ekstre yapay zekâyla okunmuştu. Aynı PDF, bankanın şablonuyla yapay zekâsız okununca rakamlar tutuyor; şu satırlar düzeltilecek:",
  korunur: "Kategori, not, eşleşme ve bölmelerin korunur. Uyguladıktan sonra geri alabilirsin.",
  uygulandi: (n: number) => `Ekstre düzeltildi · ${n} satır`,
  gizlendi: "Tamam, bu ekstre için bir daha önermeyiz",
  yok: "Bu ekstre için yeniden okuma artık yok.",
  yukleniyor: "Fark hesaplanıyor",
  rakam: (once: number, sonraTutar: boolean) =>
    sonraTutar ? `Rakamlar şimdi ${tl(Math.abs(once))} tutmuyor; düzeltilince tutuyor.` : `Rakamlar şimdi ${tl(Math.abs(once))} tutmuyor.`,
  /** sunucu fix_before (ekstre kartıyla aynı fark): özet tutuyorsa yalnız işlem listesi düzelir */
  rakamDurum: (once: { ok: boolean | null; diff: number | null }, sonraTutar: boolean) =>
    once.ok !== false || once.diff == null
      ? "Özet rakamları tutuyor; işlem listesi ekstreyle aynı olacak."
      : OKU.rakam(once.diff, sonraTutar),
};

/** Yapılacaklar / ekstre kartı satırı: "<Banka> ••1234 · N satır farklı". */
export const okuSatiri = (bank: string, last4: string, n: number) => `${bank} ••${last4} · ${OKU.satir(n)}`;

/** Kişiye özel "Şimdi değil": hatırlatma kimliği (sunucu app/statement_reread.py reminder_id). */
export const okuKimlik = (sid: number) => `ekstre_oku:${sid}`;
