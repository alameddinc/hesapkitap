// Hata → anlaşılır Türkçe neden + yapılabilecek adım (tek yer; mobil istemcide hesapkitap-mobile src/lib/hataAcikla.ts
// birebir aynı metinler — birini değiştirirsen ötekini de değiştir). Sunucunun Türkçe metni (errors.user_text) olduğu gibi gösterilir;
// İngilizce/teknik metin (sunucunun errors._TECH süzgeci + tarayıcıya özgü "Failed to fetch", "Cannot read…") asla
// gösterilmez, yerine durum koduna göre genel metin. kod: "Yardım al" bağlamına ve sorun kaydına giden kısa sabit ad.
import { ApiError } from "./apiError.ts";

export interface HataAciklama {
  /** ne oldu (tek cümle, Türkçe) */
  neden: string;
  /** kullanıcı ne yapabilir (tek cümle) */
  adim: string;
  /** sabit kısa kod (baglanti_yok, zaman_asimi, sunucu_500, gecersiz…) */
  kod: string;
  /** "Tekrar dene" anlamlı mı (ağ, sunucu, zaman aşımı) */
  tekrar: boolean;
  /** "Yardım al" bağlamı: teknik ayrıntı (yalnız destek ekranına; kullanıcıya gösterilmez) */
  ayrinti?: string;
}

// Sunucunun errors._TR / _TECH süzgeçleriyle aynı amaç. _TR'de büyük/küçük harf duyarsızlığı yalnız kelimelerde (harf
// sınıfı "i"yi Türkçe saymasın).
const TR_HARF = /[çğıöşüÇĞİÖŞÜ₺]/;
const TR_KELIME = /(^|[^\p{L}])(yok|değil|degil|için|olmalı|geçersiz|bulunamadı|yanlış|zaten|önce|sonra|şifre|kayıt|tutar|hesap|ekstre|dosya|belge|kart|borç|alacak|kişi|gün|ay|yıl|en fazla|en az|tekrar|gerekli|bu|bir|ve|ile|dene)(?=$|[^\p{L}])/iu;
const TEKNIK = /Traceback|Errno|NoneType|object has no|invalid literal|could not|Expecting|unexpected|line \d+|column \d+|not supported|is not|must be|argument|KeyError|TypeError|ValueError|ReferenceError|SyntaxError|RangeError|https?:\/\/|\bat 0x|sqlite|zipfile|openpyxl|xlrd|pdfminer|httpx|pdfplumber|pypdf|PdfReadError|PSSyntax|PDFSyntax|really a PDF|\/Root\b|\bEOF\b|startxref|xref|undefined|null\b|Cannot read|is not a function|Failed to fetch|Load failed|NetworkError|AbortError|Unexpected token|JSON|status code|ERR_[A-Z_]+|Internal Server Error|Not Found|Bad Request|Unauthorized|Forbidden|Bad Gateway|Service Unavailable|Gateway Time-?out|Request failed|Script error|capacitor|plugin|permission|denied|User cancelled|canceled|cancelled/i;

/** Metin kullanıcıya gösterilebilir mi: Türkçe, teknik ayrıntı yok, makul uzunlukta. */
export function gosterilir(msg: string | null | undefined): msg is string {
  const m = (msg ?? "").trim();
  return !!m && m.length <= 400 && !/^\d+$/.test(m) && (TR_HARF.test(m) || TR_KELIME.test(m)) && !TEKNIK.test(m);
}

/** Gösterilebilir metin ya da null (teknik/İngilizce süzülür). */
export function temizMetin(msg: string | null | undefined): string | null {
  return gosterilir(msg) ? msg.trim() : null;
}

/** Ekranda gösterilecek tek satır: neden (+ adım), sunucunun metni Türkçe ise o. */
export function hataMetni(e: unknown, yedek?: string): string {
  const a = hataAcikla(e, yedek);
  return a.neden;
}

const nokta = (s: string) => (/[.!?…]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** Hatayı açıkla. yedek: sunucu metni gösterilemezse kullanılacak ekrana özgü cümle ("Kaydedilemedi"). */
export function hataAcikla(e: unknown, yedek?: string): HataAciklama {
  const yedekNeden = yedek?.trim() || null; // uygulamanın kendi cümlesi: süzülmez ("Kaydedilemedi" Türkçe harf taşımaz)
  if (typeof e === "string") {
    // metin olarak gelen: çoğunlukla uygulamanın kendi cümlesi ("Silinemedi", "Önce tutarı yaz"; Türkçe harf taşımayabilir)
    // — yalnız teknik görünen (İngilizce hata, salt sayı) süzülür; adımı zaten söylüyor
    const m = e.trim();
    const t = m && m.length <= 400 && !/^\d+$/.test(m) && !TEKNIK.test(m) ? m : null;
    return { neden: nokta(t ?? yedekNeden ?? "Bir şey ters gitti"), adim: t ? "" : "Tekrar dene; sürerse Yardım al'a dokun.", kod: t ? "uyari" : "beklenmeyen",
      tekrar: !t, ayrinti: t ? undefined : e.slice(0, 300) };
  }
  // web: ApiError (lib/apiError.ts; body = sunucu gövdesi ya da {network, offline}) ve lib/idem.ts ağ hatası (status 0)
  const st = (e as { status?: unknown } | null)?.status;
  if (e instanceof ApiError || (e instanceof Error && typeof st === "number")) {
    const x = e as Error & { status: number; body?: unknown; offline?: boolean };
    const d = (x.body && typeof x.body === "object" ? x.body : {}) as Record<string, unknown>;
    const code = typeof d.code === "string" ? d.code : undefined;
    const sunucu = temizMetin(x.message);
    const ayrinti = `HTTP ${x.status}${code ? ` · ${code}` : ""}`;
    if (d.network || d.timeout || x.offline || (x.status === 0 && !d.aborted)) {
      if (d.offline || x.offline) return { neden: "İnternet bağlantın yok.", adim: "Bağlanınca tekrar dene; girdiklerin kaybolmaz.", kod: "baglanti_yok", tekrar: true };
      if (d.belirsiz) return { neden: "Kaydın oluşup oluşmadığı belli değil.", adim: "Listeyi yeniledik; kayıt yoksa tekrar kaydet.", kod: "belirsiz", tekrar: false };
      if (d.timeout) return { neden: "Sunucu zamanında yanıt vermedi.", adim: "Biraz bekleyip tekrar dene. Sürerse Yardım al'a dokun.", kod: "zaman_asimi", tekrar: true };
      return { neden: "Sunucuya şu an ulaşılamıyor.", adim: "Birkaç dakika sonra tekrar dene. Sürerse Yardım al'a dokun.", kod: "sunucu_yok", tekrar: true };
    }
    if (d.aborted) return { neden: "İşlem iptal edildi.", adim: "İstersen yeniden başlat.", kod: "iptal", tekrar: true };
    if (d.quota_full || d.reason === "quota")
      return { neden: nokta(sunucu ?? "Okuma hakkın doldu"), adim: "Bilgileri elle girebilir ya da ek okuma paketi alabilirsin.", kod: "okuma_hakki", tekrar: false, ayrinti };
    const s = x.status;
    if (s === 401) return { neden: nokta(sunucu ?? "Oturumun kapanmış"), adim: "Yeniden giriş yap.", kod: "oturum", tekrar: false, ayrinti };
    if (s === 402) return { neden: nokta(sunucu ?? "Bu özellik paketinde yok"), adim: "Paketler'den yükseltebilirsin.", kod: "paket", tekrar: false, ayrinti };
    if (s === 403) return { neden: nokta(sunucu ?? "Bu işlem için yetkin yok"), adim: "Ortak defterdeysen defter sahibinden yetki iste.", kod: "yetki", tekrar: false, ayrinti };
    if (s === 404) return { neden: nokta(sunucu ?? "Kayıt bulunamadı"), adim: "Listeyi yenile; başka bir cihazda silinmiş olabilir.", kod: "bulunamadi", tekrar: true, ayrinti };
    if (s === 409) return { neden: nokta(sunucu ?? "Bu kayıt az önce değişti"), adim: "Sayfayı yenileyip tekrar dene.", kod: "cakisma", tekrar: true, ayrinti };
    if (s === 413) return { neden: nokta(sunucu ?? "Dosya çok büyük"), adim: "Daha küçük bir dosya seç ya da dosyaları ayrı ayrı yükle.", kod: "dosya_buyuk", tekrar: false, ayrinti };
    if (s === 429) return { neden: nokta(sunucu ?? "Çok fazla istek gönderildi"), adim: "Biraz bekleyip tekrar dene.", kod: "cok_istek", tekrar: true, ayrinti };
    if (s === 400 || s === 422) return { neden: nokta(sunucu ?? yedekNeden ?? "Girilen bilgi geçerli değil"), adim: "Girdiğin bilgiyi kontrol edip tekrar dene.", kod: "gecersiz", tekrar: false, ayrinti };
    if (s === 503) return { neden: nokta(sunucu ?? "Sunucu şu an meşgul"), adim: "Biraz sonra tekrar dene.", kod: "sunucu_mesgul", tekrar: true, ayrinti };
    // sunucunun Türkçe metni (errors.user_text; "Sunucu şu an başka belgeleri okuyor") gösterilir, yoksa genel metin
    if (s >= 500) return { neden: nokta(sunucu ?? (s === 500 ? "Sunucuda beklenmeyen bir hata oldu" : "Bağlı bir servise şu an ulaşılamıyor")),
      adim: "Hatayı kaydettik. Biraz sonra tekrar dene; sürerse Yardım al'a dokun.", kod: `sunucu_${s}`, tekrar: true, ayrinti };
    return { neden: nokta(sunucu ?? yedekNeden ?? "İşlem tamamlanamadı"), adim: "Tekrar dene; sürerse Yardım al'a dokun.", kod: `http_${s}`, tekrar: true, ayrinti };
  }
  const err = e as { name?: string; message?: string } | null;
  const t = temizMetin(err?.message);
  return { neden: nokta(t ?? yedekNeden ?? "Bir şey ters gitti"), adim: "Tekrar dene; sürerse Yardım al'a dokun.", kod: "beklenmeyen", tekrar: true,
    ayrinti: t ? undefined : err ? `${err.name ?? "Hata"}: ${err.message ?? ""}`.slice(0, 300) : undefined };
}
