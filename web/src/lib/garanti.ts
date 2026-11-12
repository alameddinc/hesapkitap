// Garanti arşivi uçları (sunucu yeteneği "warranty", app/warranty.py). Tipler ve saf yardımcılar: ./garantiMetin.ts
import { call, send } from "./apiError";
import type { BelgeTuru, Garanti, GarantiListe, GarantiTaslak, GarantiYaz, KayitliFis } from "./garantiMetin";

export * from "./garantiMetin";

export const garanti = {
  liste: (q?: string, status?: string) => {
    const p = new URLSearchParams();
    if (q?.trim()) p.set("q", q.trim());
    if (status && status !== "all") p.set("status", status);
    const s = p.toString();
    return send<GarantiListe>(`/api/warranties${s ? `?${s}` : ""}`);
  },
  taslak: (src: { entry_id?: number; txn_id?: number }) =>
    send<GarantiTaslak>(`/api/warranties/draft?${src.entry_id != null ? `entry_id=${src.entry_id}` : `txn_id=${src.txn_id}`}`),
  fisler: (q?: string) => send<KayitliFis[]>(`/api/warranties/receipts${q?.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`),
  ekle: (b: GarantiYaz) => call<Garanti>("POST", "/api/warranties", b),
  duzenle: (id: number, b: Partial<GarantiYaz>) => call<Garanti>("PATCH", `/api/warranties/${id}`, b),
  sil: (id: number) => call<{ ok: boolean }>("DELETE", `/api/warranties/${id}`),
  belgeEkle: (id: number, file: Blob, name: string, doc_type: BelgeTuru) => {
    const fd = new FormData();
    fd.append("file", file, name);
    fd.append("doc_type", doc_type);
    return call<Garanti>("POST", `/api/warranties/${id}/files`, fd);
  },
  belgeSil: (id: number, fid: number) => call<Garanti>("DELETE", `/api/warranties/${id}/files/${fid}`),
  belgeAdresi: (id: number, fid: number, indir = false) => `/api/warranties/${id}/files/${fid}${indir ? "?download=1" : ""}`,
};

