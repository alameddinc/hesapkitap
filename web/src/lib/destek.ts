// Destek yazışması (sunucu yeteneği "support_chat"; app/support.py) ve okunamayan ekstre bildirimi ("upload_report",
// "upload_retry"). Kişi başı tek yazışma: kullanıcı mesajı (POST /api/feedback), yönetici yanıtı, sistem satırı.
//   GET  /api/support              { thread {status, unread}, messages[] }
//   GET  /api/support/unread       { unread, status }  (rozet)
//   POST /api/support/seen         yöneticinin mesajlarını okudum
//   POST /api/support/report       multipart report (JSON), bank_name, note, send_doc, file (yalnız onay açıksa)
//   GET|DELETE /api/support/pending[/{id}], POST /api/support/pending/{id}/retry {password, remember}
// yardimAl({ nerede, kod, ayrinti }): her hata ekranından tek çağrı — Destek'i bağlam ekli açar (eski sunucuda "Sorun
// bildir"). Bağlamda kişisel veri olmasın diye uzun rakam dizileri ve e-posta maskelenir, en çok 300 karakter.
import { useSyncExternalStore } from "react";
import type { UploadResult } from "../api";
import { call } from "./apiError";
import { GORSEL_EN_COK, MESAJ_EN_COK, maskeleBaglam, type ReportSummary, type YardimBaglami } from "./destekMetin";

export * from "./destekMetin";

export interface UploadReport { code: string; file_type?: string; pages?: number | null; bank_guess?: string; has_text?: boolean }
export type ReportedUpload = UploadResult & { report?: UploadReport };

export interface SupportPending { id: number; filename: string; expires_at: string }
export interface SupportMessage {
  id: number;
  author: "user" | "admin" | "system";
  kind: string | null; // null mesaj | ekstre | yanit | sistem
  body: string;
  created_at: string;
  screenshots: number;
  seen?: boolean; // kullanıcı mesajı: yönetici okudu
  report?: ReportSummary;
  pending?: SupportPending | null;
  action?: { type: "retry"; ref: number; done: boolean; pending: SupportPending | null };
  context?: Record<string, string | undefined>;
  structure?: boolean; // yönetici: maskeli yapı var
  demo?: boolean;
  status?: string;
}
export interface SupportThread {
  thread: { status: "acik" | "cozuldu"; unread: number; admin_unread: number; updated_at: string | null };
  messages: SupportMessage[];
  user?: { id: number; username: string | null; plan?: string } | null;
}
/** moved: kayıt yüklendiği ortak defter yerine "Benim" defterine gitti; nedeni */
export type RetryResult = ReportedUpload & { pending_id: number; done: boolean; moved?: string };

export const destekApi = {
  thread: () => call<SupportThread>("GET", "/api/support"),
  unread: () => call<{ unread: number; status: string | null }>("GET", "/api/support/unread"),
  seen: () => call<{ ok: true }>("POST", "/api/support/seen"),
  send: (message: string, files: File[], context: Record<string, string | undefined>) => {
    const fd = new FormData();
    fd.append("message", message.trim().slice(0, MESAJ_EN_COK));
    fd.append("context", JSON.stringify(context));
    for (const f of files.slice(0, GORSEL_EN_COK)) fd.append("screenshots", f, f.name);
    return call<{ ok: true; id: number }>("POST", "/api/feedback", fd);
  },
  report: (report: UploadReport, bank: string, note: string, file: File | null) => {
    const fd = new FormData();
    fd.append("report", JSON.stringify({ ...report, platform: "web" }));
    fd.append("bank_name", bank.trim());
    fd.append("note", note.trim());
    fd.append("send_doc", file ? "true" : "false");
    if (file) fd.append("file", file, file.name);
    return call<{ ok: true; id: number; pending: SupportPending | null }>("POST", "/api/support/report", fd);
  },
  dropPending: (id: number) => call<{ ok: true }>("DELETE", `/api/support/pending/${id}`),
  retry: (id: number, password?: string) =>
    call<RetryResult>("POST", `/api/support/pending/${id}/retry`, password ? { password, remember: true } : {}),
  shot: (id: number, n: number) => `/api/support/messages/${id}/screenshot/${n}`,
};

// ---------------------------------------------------------------- açılış durumu (her yerden)
interface Durum { open: boolean; baglam: YardimBaglami | null; no: number }
let st: Durum = { open: false, baglam: null, no: 0 };
const subs = new Set<() => void>();
const set = (p: Partial<Durum>) => { st = { ...st, ...p }; subs.forEach((f) => f()); };
export const useDestekDurumu = () => useSyncExternalStore((l) => (subs.add(l), () => { subs.delete(l); }), () => st);
export const destekAc = () => set({ open: true, baglam: null, no: st.no + 1 });
export const destekKapat = () => set({ open: false, baglam: null });

/** Hata ekranlarındaki "Yardım al": Destek bağlam ekli açılır (eski sunucuda SupportHost "Sorun bildir"e düşer). */
export function yardimAl(b: YardimBaglami) {
  set({
    open: true, no: st.no + 1,
    baglam: { nerede: maskeleBaglam(b.nerede).slice(0, 80), kod: b.kod != null ? maskeleBaglam(String(b.kod)).slice(0, 60) : null,
      ayrinti: b.ayrinti ? maskeleBaglam(b.ayrinti) : null },
  });
}

