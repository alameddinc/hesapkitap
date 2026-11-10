// Tutmayan ekstreyi düzelt (yetenek statement_fix): rozet, tek cümle, öneri ve eylemler. Cümleler mobille BİREBİR aynı
// (hesapkitap-mobile src/lib/ekstreDuzelt.ts); rozet / başlık / öneri metni sunucudan gelir (app/statement_fix.py),
// burada yalnız eski sunucu için yedek ve düğme adları.
import { tl } from "../format.ts";

export type SatirTuru = "harcama" | "iade" | "odeme" | "faiz_ucret";

/** Tür seçici: Faiz ve Ücret aynı tür (faiz_ucret), açıklaması farklı. */
export const TURLER: { key: string; label: string; type: SatirTuru; desc: string }[] = [
  { key: "harcama", label: "Harcama", type: "harcama", desc: "Harcama" },
  { key: "iade", label: "İade", type: "iade", desc: "İade" },
  { key: "odeme", label: "Ödeme", type: "odeme", desc: "Ödeme" },
  { key: "faiz", label: "Faiz", type: "faiz_ucret", desc: "Faiz" },
  { key: "ucret", label: "Ücret", type: "faiz_ucret", desc: "Ücret" },
];

export const DUZELT = {
  satirEkle: "Eksik satır ekle",
  satirlar: "Satırları düzenle",
  sablonla: (n: number) => `Şablonla düzelt (${n} satır)`,
  onayla: "Rakamlar doğru, onayla",
  pdf: "PDF'i aç",
  tuttu: "Tuttu ✓",
  kalan: (d: number) => `Kalan fark ${tl(Math.abs(d))}`,
  bununla: "Bu değişiklikle:",
  hesaplaniyor: "Hesaplanıyor…",
  eklendi: "Satır eklendi",
  duzeltildi: "Satır düzeltildi",
  tutar: "Tutar",
  tur: "Tür",
  tarih: "Tarih",
  aciklama: "Açıklama",
  kaydet: "Kaydet",
  ekle: "Ekle",
  hesabaKatma: "Hesaba katma",
  hesabaKat: "Hesaba kat",
  katilmiyor: "hesaba katılmıyor",
  oneri: "Öneri",
  bulunamadi: "Bu ekstrede satır yok.",
  isaretNotu: "Tutarı artı yaz; iade ve ödeme eksi sayılır.",
  iadeSatiri: "− İade / kampanya",
  // durum rozetleri: Tuttu ✓ · İşlemler eksik / fazla · Özet tutmuyor (sunucu) · Onaylandı (elle)
  rozetTuttu: "Tuttu ✓",
  rozetOnay: "Onaylandı (elle)",
  yenidenKontrol: "Yeniden kontrol et",
  onayMetni: (ne?: string | null) => `Rakamları ekstreyle karşılaştırıp doğru dedin${ne ? ` (${ne})` : ""}.`,
  pdfYan: "PDF'i yanında açıp satırları tek tek karşılaştır.",
  satirDokun: "Düzeltmek için satıra dokun.",
  grupAc: (label: string) => `${label}: satırları göster`,
  tumSatirlar: "Bütün satırları göster",
  // eski sunucu (check_kind gelmez): eski cümle
  eskiBaslik: (d: number) => `Rakamlar ${tl(Math.abs(d))} tutmadı.`,
  tuttuMetni: "Rakamlar tuttu: ekstrenin özeti ve işlemleri aynı.",
};

/** Satırın türüne göre tür seçicinin anahtarı (faiz_ucret: açıklamada "ücret" geçiyorsa Ücret). */
export function turAnahtari(type: string, description?: string | null): string {
  if (type === "faiz_ucret") return /ücret|ucret|aidat|komisyon/i.test(description ?? "") ? "ucret" : "faiz";
  return TURLER.some((t) => t.key === type) ? type : "harcama";
}

/** Canlı önizleme cümlesi: "Bu değişiklikle: Tuttu ✓" ya da "Bu değişiklikle: Kalan fark ₺10,00". */
export function onizleme(check: { ok: boolean | null; diff: number | null } | null | undefined): string | null {
  if (!check) return null;
  if (check.ok !== false) return `${DUZELT.bununla} ${DUZELT.tuttu}`;
  return `${DUZELT.bununla} ${DUZELT.kalan(check.diff ?? 0)}`;
}

/** Kayıt sonrası bildirim: "Satır eklendi · Tuttu ✓" / "Satır düzeltildi · Kalan fark ₺10,00". */
export function sonucMetni(eylem: "eklendi" | "duzeltildi", check: { ok: boolean | null; diff: number | null }): string {
  return `${DUZELT[eylem]} · ${check.ok !== false ? DUZELT.tuttu : DUZELT.kalan(check.diff ?? 0)}`;
}

/** Tutar girişi: "1.234,50" / "1234.50" / "30" → sayı (geçersiz / sıfır → null). */
export function tutarOku(v: string): number | null {
  const s = v.trim().replace(/\s|₺|TL/gi, "");
  if (!s) return null;
  const n = Number(s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s);
  return Number.isFinite(n) && Math.abs(n) >= 0.01 ? Math.round(Math.abs(n) * 100) / 100 : null;
}

/** Tutar kutusunun ilk değeri: 30 → "30,00". */
export const tutarYaz = (n: number | null | undefined) => (n == null ? "" : Math.abs(n).toFixed(2).replace(".", ","));

// ---------------------------------------------------------------- satır grupları (ekstre ayrıntısı)

export type GrupKey = "harcama" | "taksit" | "ekkart" | "iade" | "odeme" | "faiz";
export const GRUPLAR: { key: GrupKey; label: string }[] = [
  { key: "harcama", label: "Harcamalar" },
  { key: "taksit", label: "Taksitler" },
  { key: "ekkart", label: "Ek kart" },
  { key: "iade", label: "İadeler" },
  { key: "odeme", label: "Ödemeler" },
  { key: "faiz", label: "Faiz ve ücretler" },
];
/** Denklem kutusundaki rakama dokununca açılacak gruplar. */
export const BILESEN: Record<"harcama" | "odeme" | "faiz" | "iade", GrupKey[]> = {
  harcama: ["harcama", "taksit", "ekkart", "iade"], odeme: ["odeme"], faiz: ["faiz"], iade: ["iade"],
};

const son4 = (s: string | null | undefined) => (s ?? "").replace(/\D/g, "").slice(-4);

/** Satırın grubu: tür önce (ödeme, iade, faiz); harcamada ek kart (son 4 hane ana karttan farklı), sonra taksit. */
export function grupOf(r: { txn_type: string; installment_no?: number | null; card?: string | null }, anaKart: string): GrupKey {
  if (r.txn_type === "odeme") return "odeme";
  if (r.txn_type === "iade") return "iade";
  if (r.txn_type === "faiz_ucret") return "faiz";
  const k = son4(r.card);
  if (k && son4(anaKart) && k !== son4(anaKart)) return "ekkart";
  return r.installment_no ? "taksit" : "harcama";
}

/** Gruplar (boş olanlar atlanır). odak verilirse yalnız o gruplar. Toplam yok: para rakamı sunucuda (parity C7). */
export function grupla<T extends { txn_type: string; installment_no?: number | null; card?: string | null }>(
  rows: T[], anaKart: string, odak?: GrupKey[] | null,
): { key: GrupKey; label: string; rows: T[] }[] {
  return GRUPLAR.filter((g) => !odak || odak.includes(g.key))
    .map((g) => ({ ...g, rows: rows.filter((r) => grupOf(r, anaKart) === g.key) }))
    .filter((g) => g.rows.length > 0);
}
