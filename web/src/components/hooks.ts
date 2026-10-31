import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { newKey, withAction } from "../lib/idem";
import { api, type Row } from "../api";
import { addMonths, periodOf } from "../format";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: api.meta });

/** Her yazma işleminden sonra tüm sorguları tazele: veri küçük, basitlik daha değerli. */
export function useSave<A, R>(fn: (a: A) => Promise<R>, onDone?: (r: R) => void) {
  const qc = useQueryClient();
  // eylem anahtarı (Idempotency-Key): başarıya kadar aynı kalır — ağ hatasından sonra tekrar basılan "Kaydet"
  // aynı anahtarla gider, sunucu işlemi ikinci kez yapmaz; başarıdan sonra sıradaki eylem yeni anahtar alır
  const key = useRef<string | null>(null);
  return useMutation({
    mutationFn: (a: A) => withAction(key.current ??= newKey(), () => fn(a)),
    onSuccess: (r) => {
      key.current = null;
      qc.invalidateQueries();
      onDone?.(r);
    },
  });
}

/** Ortak defter: bu kullanıcı başkasının defterinin üyesi mi, hangi defter açık, yetkisi, neler kapalı (sunucu da ayrıca uygular). */
export type BookRole = "viewer" | "editor" | "full";
export interface BookPerson { id: number; name: string; owner: boolean; role: BookRole | null }
export interface BookInfo {
  member: boolean; me?: number | null; owner?: string; role?: BookRole; role_label?: string; book?: "ortak" | "kendi"; can_switch?: boolean;
  people?: BookPerson[]; hidden_pages?: string[]; hide_fees?: boolean;
}

export const useBook = () => useQuery({
  queryKey: ["book-me"], staleTime: 60_000,
  queryFn: () => fetch("/api/book/me").then((r) => (r.ok ? r.json() : { member: false }) as Promise<BookInfo>),
});

/** Üye ortak defterde mi? (kendi defterinde kısıt yok) */
export const inSharedBook = (b?: BookInfo) => !!b?.member && b.book !== "kendi";
/** Ortak defterde "Sadece görür": ekleme yok. */
export const isViewer = (b?: BookInfo) => inSharedBook(b) && b?.role === "viewer";
/** Silme, gizleme gibi sahip işleri kapalı mı? (tam yetkili üye yapabilir) */
export const isLimited = (b?: BookInfo) => inSharedBook(b) && b?.role !== "full";

export const BOOK_COOKIE = "hk_book";
/** Üye: ortak defter ile kendi defteri arasında geçiş. Çerez her isteğe gider; önbellek boşaltılır, veri yeniden gelir. */
export function useSwitchBook() {
  const qc = useQueryClient();
  return (to: "ortak" | "kendi") => {
    document.cookie = to === "kendi" ? `${BOOK_COOKIE}=kendi; path=/; max-age=31536000; SameSite=Lax`
      : `${BOOK_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
    window.location.hash = "/ozet";
    // önbelleği boşalt (clear gibi) ama ekrandaki sorgular bağlı kalsın: veri yoktan, yeni defterden yeniden gelir
    // (oturum bilgisi kişiye ait, defterle değişmez: sıfırlanırsa tüm ekran yeniden kurulurdu)
    const book = (q: { queryKey: readonly unknown[] }) => q.queryKey[0] !== "auth";
    qc.removeQueries({ type: "inactive", predicate: book });
    qc.getMutationCache().clear();
    qc.resetQueries({ predicate: book });
  };
}

/** Giriş alanı türü: hazır sürümde "email", kendi sunucusunda "username" (eski sunucu alanı vermez → "username"). */
export type LoginKind = "email" | "username";
export const loginKind = (status?: { login?: string } | null): LoginKind => (status?.login === "email" ? "email" : "username");
export const isEmail = (s: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim());
export function useLoginKind(): LoginKind {
  const auth = useQuery({
    queryKey: ["auth"], staleTime: 60_000,
    queryFn: () => fetch("/api/auth/status").then((r) => r.json() as Promise<{ login?: string }>),
  });
  return loginKind(auth.data);
}

/** Kayıtlarda "kim ekledi": defterde birden çok kişi varken kişi adları (üyenin kendi defterinde gerekmez). */
export function usePeople() {
  const b = useBook().data;
  const auth = useQuery({
    queryKey: ["auth"], staleTime: 60_000,
    queryFn: () => fetch("/api/auth/status").then((r) => r.json() as Promise<{ user?: { username: string } | null }>),
  });
  const people = b?.book === "kendi" ? [] : b?.people ?? [];
  // sunucu "me" verir; eski sunucuda ada göre tahmin
  const myName = auth.data?.user?.username.split("@")[0];
  const meId = b?.me ?? (b?.member ? people.find((p) => p.name === myName) : people.find((p) => p.owner))?.id;
  const ownerId = people.find((p) => p.owner)?.id;
  const multi = people.length > 1;
  /** created_by boşsa (eski kayıt) sahibin kaydıdır. */
  const person = (id?: number | null) => people.find((p) => p.id === (id ?? ownerId));
  return { multi, people, meId, person, isMe: (id?: number | null) => (id ?? ownerId) === meId };
}

const MEMBER_HIDDEN = ["kurallar", "ekstreler"]; // yükleme ve kurallar sahibe ait (tam yetkiliye açık)

/** Sayfa menüde görünsün mü: ortak defter kuralları + kullanıcının kapattığı özellikler. */
export const routeVisible = (b: BookInfo | undefined, route: string, modules?: Record<string, boolean>) =>
  (!inSharedBook(b) || !((isLimited(b) && MEMBER_HIDDEN.includes(route)) || b?.hidden_pages?.includes(route)))
  && modules?.[route] !== false;

/** Sahip ve en az bir üyesi var mı? (işlemde "Ortak defterde gizle" seçeneği o zaman görünür) */
export const useHasMembers = () => useQuery({
  queryKey: ["book"], retry: false, staleTime: 60_000,
  queryFn: () => fetch("/api/book").then((r) => (r.ok ? r.json() : null) as Promise<{ members: unknown[] } | null>),
  select: (d) => !!d?.members.length,
});

/** Canlı demo: süre ve örnek dosyalar (demo değilse null). */
export interface DemoInfo { expires_at: number; ttl: number; samples: { key: string; label: string; kind: "statement" | "receipt" }[] }

export const useDemo = () => useQuery({
  queryKey: ["demo"], staleTime: Infinity, retry: false,
  queryFn: () => fetch("/api/demo").then((r) => (r.ok ? r.json() : null) as Promise<DemoInfo | null>),
});

/** Kişisel görünüm (sade mod, Özet düzeni): sunucuda kişi bazında saklanır. */
export function useUiPrefs() {
  const meta = useMeta();
  const qc = useQueryClient();
  const ui = meta.data?.ui ?? { simple: false, layout: null };
  const save = useMutation({
    mutationFn: (body: { simple?: boolean; layout?: string[] | null; reset_layout?: boolean }) =>
      fetch("/api/me/ui", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then((r) => r.json()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["meta"] }),
  });
  return { ui, save };
}

/** Kaydı düzenleme penceresinde aç: o dönemin işlem listesinden satırı uid ile bulur ("m12" elle kayıt, "c34" kart
 *  işlemi). Kart işleminin dönemi ekstreye göre kayabilir: tarihin dönemi, takvim ayı ve sonraki dönem denenir.
 *  Bulamazsa (ör. silinmiş) false döner. */
export function useOpenRow() {
  const qc = useQueryClient();
  const meta = useMeta().data;
  return async (uid: string, date: string, open: (row: Row) => void) => {
    const month = periodOf(date, meta?.cycle_start_day ?? 1);
    for (const m of [...new Set([month, date.slice(0, 7), addMonths(month, 1)])]) {
      const rows = await qc.fetchQuery({ queryKey: ["txns", m], queryFn: () => api.transactions(m), staleTime: 10_000 });
      const row = rows.find((r) => r.uid === uid);
      if (row) { open(row); return true; }
    }
    return false;
  };
}

/** Elle kaydı (entries.id) düzenleme penceresinde aç (Transferler). */
export function useOpenEntry() {
  const openRow = useOpenRow();
  return (id: number, date: string, open: (row: Row) => void) => openRow(`m${id}`, date, open);
}
