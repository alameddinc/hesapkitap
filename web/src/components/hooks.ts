import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: api.meta });

/** Her yazma işleminden sonra tüm sorguları tazele: veri küçük, basitlik daha değerli. */
export function useSave<A, R>(fn: (a: A) => Promise<R>, onDone?: (r: R) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      qc.invalidateQueries();
      onDone?.(r);
    },
  });
}

/** Ortak defter: bu kullanıcı başkasının defterinin üyesi mi, neler kapalı (sunucu da ayrıca uygular). */
export interface BookInfo { member: boolean; owner?: string; hidden_pages?: string[]; hide_fees?: boolean }

export const useBook = () => useQuery({
  queryKey: ["book-me"], staleTime: 60_000,
  queryFn: () => fetch("/api/book/me").then((r) => (r.ok ? r.json() : { member: false }) as Promise<BookInfo>),
});

const MEMBER_HIDDEN = ["kurallar", "ekstreler"]; // yükleme ve kurallar sahibe ait

/** Sayfa menüde görünsün mü: ortak defter kuralları + kullanıcının kapattığı özellikler. */
export const routeVisible = (b: BookInfo | undefined, route: string, modules?: Record<string, boolean>) =>
  (!b?.member || !(MEMBER_HIDDEN.includes(route) || b.hidden_pages?.includes(route))) && modules?.[route] !== false;

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
