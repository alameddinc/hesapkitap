// Ekstreler → toplu yüklemede okunan fiş/fatura (yetenek upload_docs; mobil components/yukleme/kuyruk-durum.ts "document"
// ile aynı akış). Sunucu ekstre olmayan fotoğrafı / fatura PDF'ini belge olarak okur ve {status "document", doc_type, doc}
// döner; okuma hakkı orada bir kez düşer, burada yeniden okunmaz. Satır "Fatura okundu — onayla": Onayla → fatura onay
// formu (Ödemelere ekle) ya da gider formu okunmuş belgeyle dolu açılır; Vazgeç → satır gider.
// Onay bekleyenler sayfadan bağımsız bellekte durur: Ekstreler'den çıkılsa da Özet → Yapılacaklar'da "Okunan fatura onay
// bekliyor" satırı onlara götürür (sayfa yenilenince kaybolur; mobildeki kuyruk gibi).
// Saf yardımcılar + küçük depo: React'e bağlı değil (node --test).
import type { BillDraft, UploadResult } from "../api";

export interface BekleyenBelge {
  key: string;
  result: UploadResult; // status "document" (doc dolu)
  file: File | null; // fotoğraf önizlemesi (gider formunda)
}

/** Yükleme sonucunun istemci anahtarı (aynı adlı iki dosya karışmasın); yalnız "document" satırlarına verilir. */
export type Satir = UploadResult & { belgeKey?: string };

let liste: BekleyenBelge[] = [];
let seq = 0;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export const bekleyenBelgeler = () => liste;
export const belgeAbone = (f: () => void) => (subs.add(f), () => { subs.delete(f); });
export const belgeBekliyor = (key: string | undefined) => !!key && liste.some((b) => b.key === key);

/** Gelen sonuçlardaki okunmuş fiş/faturayı anahtarla, onay bekleyenlere ekle (r nesnesine belgeKey yazılır). */
export function belgeleriAl(res: Satir[], fileOf: (name: string) => File | null = () => null) {
  const yeni = res.filter((r) => r.status === "document" && r.doc && !r.belgeKey);
  if (!yeni.length) return;
  for (const r of yeni) r.belgeKey = `b${++seq}`;
  liste = [...liste, ...yeni.map((r) => ({ key: r.belgeKey!, result: r, file: fileOf(r.filename) }))];
  emit();
}

/** Onay formu açıldı ya da vazgeçildi: bekleyenlerden düşer. */
export function belgeSil(key: string | undefined) {
  if (!key || !liste.some((b) => b.key === key)) return;
  liste = liste.filter((b) => b.key !== key);
  emit();
}

/** Vazgeç onayı (okuma hakkı zaten düştü; mobil kuyruk-durum ile aynı söz). */
export const VAZGEC_SORU = "Okunan belge kaydedilmeyecek. Vazgeçilsin mi?\n\nBu okuma hakkından düştü; vazgeçersen gider ya da fatura olarak eklenmez.";
export const VAZGEC_OPT = { confirm: "Evet, vazgeç", cancel: "Hayır", danger: true } as const;

/** Testler için: depoyu boşalt. */
export function sifirlaTest() { liste = []; seq = 0; emit(); }

export const isFatura = (r: UploadResult) => r.doc_type === "bill" || r.doc?.type === "bill";

/** Satır metni (sunucu message verir; vermezse aynısı). */
export const durumMetni = (r: UploadResult) => r.message || (isFatura(r) ? "Fatura okundu — onayla" : "Fiş okundu — onayla");

/** Özet: işyeri / kurum, tutar, tarih (tutar biçimi çağıranda: göz kapalıysa ••••). */
export function belgeOzet(r: UploadResult): { ad: string | null; tutar: number | null; tarih: string | null; tarihEtiket: string } {
  const d = r.doc;
  if (!d) return { ad: null, tutar: null, tarih: null, tarihEtiket: "" };
  return isFatura(r)
    ? { ad: d.provider || d.description || null, tutar: d.amount ?? null, tarih: d.due_date ?? null, tarihEtiket: "son ödeme" }
    : { ad: d.merchant || null, tutar: d.total ?? null, tarih: d.date ?? null, tarihEtiket: "" };
}

/** Yapılacaklar satırı: başlık ve alt metin (mobil Yapilacaklar BekleyenBelgeler ile aynı sözler). */
export const yapilacakBaslik = (r: UploadResult) => (isFatura(r) ? "Okunan fatura onay bekliyor" : "Okunan fiş onay bekliyor");
export const yapilacakNeden = (r: UploadResult) => (isFatura(r) ? "ödemelere eklemek için onayla" : "gider olarak kaydetmek için onayla");

/** Okunan faturayı fatura onay formuna (Ödemelere ekle) taslak olarak çevir (Ekle ve Gelen kutusu ile aynı alanlar). */
export function faturaTaslak(r: UploadResult): BillDraft {
  const d = r.doc!;
  return { provider: d.provider, amount: d.amount ?? undefined, due_date: d.due_date ?? undefined, category: d.category,
    description: d.description, file_name: d.file_name, confidence: d.confidence, cached: !!d.cached };
}

/** Dosya seçimi: docs açıkken fotoğraf (fiş) da gönderilir; yoksa yalnız ekstre / döküm uzantıları (eski davranış). */
export function gonderilir(f: { name: string; type: string }, docs: boolean): boolean {
  if (/\.(pdf|xlsx|xls|csv)$/i.test(f.name)) return true;
  return docs && (f.type.startsWith("image/") || /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name));
}
