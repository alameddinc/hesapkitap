// Idempotency-Key (sunucu cf8d1be): ağ zaman aşımından sonra tekrar gönderilen yazma işlemi çift kayıt açmasın.
// Uygulamanın tüm yazma istekleri (POST/PATCH/PUT/DELETE /api/*) fetch üzerinden geçtiği için burada tek yerde:
// - her istek bir anahtar alır; kullanıcı eylemi içindeyse (useSave → withAction) anahtar eylemden türer:
//   "Kaydet" ağ hatasıyla düştükten sonra tekrar basılınca aynı anahtarlar gider, sunucu ilk yanıtı döner
// - ağ hatasında (yanıt hiç gelmedi) aynı anahtarla 2 kez daha denenir (çevrimdışıysa denenmez)
// - 409 + Retry-After (aynı istek hâlâ sürüyor): bekleyip aynı anahtarla yeniden; olmazsa listeler tazelenir
// - 422 "başka bir istek için kullanıldı" (eylem tekrarında gövde değişti): yeni anahtarla bir kez gönderilir
// Toplu yüklemede her parti ayrı istek: parti başına anahtar.

import { networkText } from "./apiError";

const WRITE = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const HEADER = "Idempotency-Key";

export const newKey = (): string => {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch { /* güvenli olmayan bağlam */ }
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
};

/** Süren kullanıcı eylemi: anahtar + eylem içindeki istek sırası (bir eylem birden çok istek atabilir). */
let action: { key: string; n: number } | null = null;

/** fn içinde atılan yazma istekleri bu eylemin anahtarından türeyen anahtarları kullanır ("<anahtar>:1", ":2"…). */
export async function withAction<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const prev = action;
  action = { key, n: 0 };
  try { return await fn(); } finally { action = prev; }
}

/** Yanıt alınamadı: Türkçe metinli hata (status 0). */
const netError = (cause?: unknown) => Object.assign(new Error(networkText()), { status: 0, cause });

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let onStuck: (() => void) | null = null;
/** 409 bitmeyince (işlem sürüyor) çağrılır: listeler tazelensin. */
export const setStuckHandler = (fn: (() => void) | null) => { onStuck = fn; };

function method(input: RequestInfo | URL, init?: RequestInit) {
  return (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
}
function path(input: RequestInfo | URL) {
  const u = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  try { return new URL(u, location.href).pathname; } catch { return u; }
}

/** window.fetch'i sarar (main.tsx bir kez çağırır). */
export function installIdempotency() {
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (!path(input).startsWith("/api/") || input instanceof Request) return orig(input, init);
    // okuma: yeniden deneme yok; ağ hatası İngilizce "Failed to fetch" yerine Türkçe (çevrimdışı / sunucu)
    if (!WRITE.has(method(input, init))) return orig(input, init).catch(() => { throw netError(); });
    const headers = new Headers(init?.headers);
    if (!headers.has(HEADER)) headers.set(HEADER, action ? `${action.key}:${++action.n}` : newKey());
    const send = () => orig(input, { ...init, headers });
    let net = 0, busy = 0, swapped = false;
    for (;;) {
      let res: Response;
      try {
        res = await send();
      } catch (e) {
        // yanıt gelmedi: istek sunucuda işlenmiş olabilir — aynı anahtarla yeniden (sunucu ilk yanıtı döner)
        if (navigator.onLine === false || ++net > 2) throw netError(e);
        await sleep(800 * net);
        continue;
      }
      if (res.status === 409 && res.headers.has("Retry-After")) {
        if (++busy > 4) { onStuck?.(); return res; }
        await sleep(Math.min(10, Number(res.headers.get("Retry-After")) || 2) * 1000);
        continue;
      }
      if (res.status === 422 && !swapped) {
        const j = await res.clone().json().catch(() => null) as { detail?: unknown } | null;
        if (typeof j?.detail === "string" && j.detail.includes(HEADER)) {
          swapped = true; // aynı eylemin tekrarında gövde değişti: bu artık yeni bir istek
          headers.set(HEADER, newKey());
          continue;
        }
      }
      return res;
    }
  };
}
