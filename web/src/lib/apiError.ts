// Sunucu hata gövdesi → kullanıcıya gösterilecek Türkçe cümle (sunucu c80eb0f sözleşmesi):
// - 422: detail Türkçe metin (alan/tür ayrıca errors:[{loc, type}]); eski sunucuda pydantic dizisi → burada Türkçeleşir
// - 404: refresh: true → ilgili listeler tazelenir (kayıt başka cihazda silinmiş/değişmiş olabilir), sunucunun metni gösterilir
// - makine kodları (consent_required, password_change_required, setup_required, account_pending_deletion): detail kodun
//   kendisi; yanında code ve Türkçe message → kullanıcıya message, davranış code'a göre (ilgili ekranlar code'a bakar)
// Ham JSON ya da "[object Object]" hiçbir zaman görünmez.

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

/** Sunucunun makine kodları: message gelmezse gösterilecek Türkçe karşılık. */
const CODES: Record<string, string> = {
  consent_required: "Devam etmek için koşulları onaylaman gerekiyor",
  password_change_required: "Devam etmeden önce şifreni değiştirmen gerekiyor",
  setup_required: "Kurulum tamamlanmamış; ilk kurulumu bitir",
  account_pending_deletion: "Bu hesap silinmek üzere; geri almak için giriş ekranındaki yönergeyi izle",
};

type Body = { detail?: unknown; message?: unknown; code?: unknown; refresh?: unknown } | null | undefined;

let onRefresh: (() => void) | null = null;
/** 404 + refresh: true geldiğinde çağrılır (main.tsx: tüm sorguları tazeler). */
export const setRefreshHandler = (fn: (() => void) | null) => { onRefresh = fn; };

/** Hata gövdesinin makine kodu (yoksa null): davranış buna göre seçilir. */
export const errorCode = (b: Body): string | null =>
  typeof b?.code === "string" ? b.code : typeof b?.detail === "string" && b.detail in CODES ? b.detail : null;

/** Tüm hata gövdesi → Türkçe metin; 404 refresh'te listeleri tazeler. */
export function errorText(b: Body, status: number): string {
  if (b?.refresh === true) onRefresh?.();
  const code = errorCode(b);
  if (code) return typeof b?.message === "string" && b.message ? b.message : CODES[code] ?? detailText(undefined, status);
  return detailText(b?.detail, status);
}

/** Ağ hatası: çevrimdışı mı, sunucu mu? (fetch hiç yanıt alamadı) */
export function networkText(): string {
  return typeof navigator !== "undefined" && navigator.onLine === false
    ? "İnternet bağlantın yok. Bağlanınca tekrar dene."
    : "Sunucuya ulaşılamadı. İnternetin çalışıyorsa sunucu geçici olarak kapalı olabilir; biraz sonra tekrar dene.";
}

/** Vekil/sunucu yanıt vermiyor (502/503/504, gövde JSON değil). */
const GATEWAY = new Set([502, 503, 504]);

/** Başarılı (2xx) ama JSON olmayan yanıt: vekilin HTML sayfası, boş gövde, eski önbellekteki index.html. */
export const UNEXPECTED = "Sunucudan beklenmeyen bir yanıt geldi; sayfayı yenileyip tekrar dene.";

/** Sunucu hatası: Türkçe metin, HTTP durumu (0: yanıt alınamadı) ve varsa çözülmüş gövde. */
export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown = null) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/** Hata yanıtının Türkçe metni (gövde JSON değilse durum koduna göre). */
export function failureText(status: number, body: unknown): string {
  if (body && typeof body === "object") return errorText(body as Body, status);
  return GATEWAY.has(status) ? "Sunucu şu an yanıt vermiyor; biraz sonra tekrar dene." : detailText(undefined, status);
}

/** Yanıtın JSON gövdesi; çözülemezse null (fırlatmaz). */
export async function bodyOf(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => "");
  if (!text) return null;
  try { return JSON.parse(text); } catch { return null; }
}

/** Yanıt → gövde. Hata yanıtı ve JSON olmayan başarılı yanıt Türkçe metinli ApiError olarak fırlar
 *  ("Unexpected token '<'" ya da "[object Object]" hiçbir zaman görünmez). */
export async function readBody<T = unknown>(res: Response): Promise<T> {
  const body = await bodyOf(res);
  if (!res.ok) throw new ApiError(failureText(res.status, body), res.status, body);
  if (body === null) throw new ApiError(UNEXPECTED, res.status);
  return body as T;
}

/** fetch + JSON gövde; ağ hatası ve hata yanıtı Türkçe metinli ApiError olarak fırlar (status, body ekli).
 *  Çağıran try/finally ile "meşgul" durumunu her koşulda bırakır. */
export async function send<T = unknown>(url: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  if (json !== undefined) { rest.body = JSON.stringify(json); rest.headers = { "Content-Type": "application/json", ...rest.headers }; }
  let res: Response;
  try {
    res = await fetch(url, rest);
  } catch {
    throw new ApiError(networkText(), 0);
  }
  return readBody<T>(res);
}

/** Bileşenlerdeki eski json(method, url, body) yardımcılarının ortak karşılığı. */
export const call = <T = unknown>(method: string, url: string, body?: unknown): Promise<T> =>
  send<T>(url, body instanceof FormData ? { method, body } : { method, json: body });
