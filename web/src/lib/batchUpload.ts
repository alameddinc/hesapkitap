// Ekstre / döküm yükleme: istemcide hazırlık ve partiler. Sunucu istek başına en fazla 30 dosya, dosya başı 15 MB kabul
// eder ve aynı anda 2 belge okur (app/limits.py); büyük seçim tek istekte 413 alır. Burada:
// - 15 MB üstü dosya gönderilmez, sonuçta nedeniyle görünür
// - aynı içerikli dosyalar (SHA-256) bir kez gönderilir
// - en fazla 5'erli partiler sırayla gider; bir parti hata verirse sonrakiler sürer, o partinin dosyaları nedeniyle listelenir
import { api, errStatus, type DocKind, type UploadResult } from "../api";

export const MAX_FILE_MB = 15;
export const BATCH = 5;

/** 413 / 503 ve ağ hatası için anlamlı Türkçe neden. */
export function uploadError(e: unknown): string {
  const s = errStatus(e);
  const msg = (e as Error)?.message ?? "";
  const server = msg && !/^\d{3}\b/.test(msg) ? msg : "";
  if (s === 413) return server || `Dosya çok büyük ya da bir seferde çok dosya (en fazla ${MAX_FILE_MB} MB)`;
  if (s === 503) return "Sunucu şu an başka belgeleri okuyor; biraz sonra bu dosyayı yeniden yükle";
  if (s === 504 || s === 502) return "Okuma zaman aşımına uğradı; dosyayı tek başına yeniden yükle";
  if (!s) return "Bağlantı koptu; bu dosyayı yeniden yükle";
  return server || "Okunamadı";
}

async function sha256(f: File): Promise<string | null> {
  try {
    const buf = await f.arrayBuffer();
    const d = await crypto.subtle.digest("SHA-256", buf);
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null; // güvenli bağlam yok (http): tekilleştirme yapılmaz, sunucu yine aynı belgeyi "zaten yüklü" der
  }
}

/** Gönderilecekler ve hiç gönderilmeyenlerin sonuçları (büyük / aynı dosya). */
export async function prepare(files: File[]): Promise<{ send: File[]; skipped: UploadResult[] }> {
  const send: File[] = [];
  const skipped: UploadResult[] = [];
  const seen = new Map<string, string>();
  for (const f of files) {
    if (f.size > MAX_FILE_MB * 1024 * 1024) {
      skipped.push({ filename: f.name, status: "error", message: `Dosya ${MAX_FILE_MB} MB'tan büyük; gönderilmedi (${(f.size / 1048576).toFixed(1).replace(".", ",")} MB)` });
      continue;
    }
    const h = await sha256(f);
    if (h && seen.has(h)) {
      skipped.push({ filename: f.name, status: "duplicate", message: `"${seen.get(h)}" ile aynı içerikte; yalnız o gönderildi` });
      continue;
    }
    if (h) seen.set(h, f.name);
    send.push(f);
  }
  return { send, skipped };
}

/** Partileri sırayla gönder; onProgress(gönderilen, toplam) her parti sonunda. */
export async function uploadInBatches(files: File[], opts: {
  password?: string; remember?: boolean; kind?: DocKind;
  onProgress?: (done: number, total: number) => void;
  onBatch?: (res: UploadResult[]) => void;
}): Promise<UploadResult[]> {
  const out: UploadResult[] = [];
  opts.onProgress?.(0, files.length);
  for (let i = 0; i < files.length; i += BATCH) {
    const part = files.slice(i, i + BATCH);
    let res: UploadResult[];
    try {
      res = await api.upload(part, opts.password, opts.remember, opts.kind);
    } catch (e) {
      const why = uploadError(e);
      res = part.map((f) => ({ filename: f.name, status: "error" as const, message: why }));
    }
    out.push(...res);
    opts.onBatch?.(res);
    opts.onProgress?.(Math.min(i + BATCH, files.length), files.length);
  }
  return out;
}
