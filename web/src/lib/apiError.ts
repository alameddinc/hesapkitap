// Sunucu hata gövdesindeki "detail" → kullanıcıya gösterilecek Türkçe cümle. Uçlarımız Türkçe metin döner (string);
// FastAPI/pydantic doğrulama hatası ise dizi gelir ([{loc, msg, type}]) — ham JSON ya da "[object Object]" görünmesin.

const FIELD: Record<string, string> = {
  amount: "Tutar", paid_at: "Ödeme tarihi", debt_date: "Tarih", due_date: "Vade", entry_date: "Tarih", person: "Kişi",
  name: "Ad", category: "Kategori", description: "Açıklama", direction: "Yön", asset: "Birim", account_id: "Hesap",
  splits: "Bölme", items: "Kalemler", note: "Not", balance: "Bakiye", monthly_limit: "Limit", provider: "Kurum",
};

interface PydErr { loc?: (string | number)[]; msg?: string; type?: string; ctx?: Record<string, unknown> }

function one(e: PydErr): string {
  const key = [...(e.loc ?? [])].reverse().find((x) => typeof x === "string" && x !== "body") as string | undefined;
  const f = key ? FIELD[key] ?? key : "Bir alan";
  const c = e.ctx ?? {};
  switch (e.type) {
    case "missing": return `${f} gerekli`;
    case "greater_than": return `${f} ${c.gt ?? 0}'dan büyük olmalı`;
    case "greater_than_equal": return `${f} en az ${c.ge ?? 0} olmalı`;
    case "less_than_equal": return `${f} en fazla ${c.le} olmalı`;
    case "string_too_short": return `${f} boş olamaz`;
    case "string_too_long": return `${f} çok uzun (en fazla ${c.max_length} karakter)`;
    case "float_parsing": case "int_parsing": case "decimal_parsing": return `${f} bir sayı olmalı`;
    case "date_parsing": case "date_from_datetime_parsing": case "date_type": return `${f} geçerli bir tarih olmalı`;
    case "literal_error": case "enum": return `${f} için geçersiz seçim`;
    case "value_error": return e.msg?.replace(/^Value error,\s*/, "") || `${f} geçersiz`;
    default: return `${f} geçersiz`;
  }
}

export function detailText(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) return [...new Set(detail.map((d) => one(d as PydErr)))].join("; ");
  if (detail && typeof detail === "object" && "message" in detail && typeof (detail as { message: unknown }).message === "string")
    return (detail as { message: string }).message;
  if (status === 404) return "Kayıt bulunamadı (silinmiş olabilir)";
  if (status === 409) return "Bu kayıt bu arada değişti; sayfayı yenileyip tekrar dene";
  if (status === 413) return "Dosya çok büyük";
  if (status === 403) return "Bu işlem için yetkin yok";
  if (status === 422) return "Girilen bilgi geçersiz";
  if (status === 429) return "Çok sık denendi; biraz sonra tekrar dene";
  if (status >= 500) return "Sunucuda bir sorun oldu; biraz sonra tekrar dene";
  return `İşlem yapılamadı (${status})`;
}
