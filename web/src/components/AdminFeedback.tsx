import { useQuery } from "@tanstack/react-query";
import { MessageSquareWarning } from "lucide-react";
import { useState } from "react";
import { call } from "../lib/apiError";
import { cx, dateTimeTR, parseTs } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

// Yönetici: kullanıcıların "Sorun bildir" mesajları (GET /api/admin/feedback?status, PATCH /api/admin/feedback/{id},
// GET /api/admin/feedback/{id}/screenshot/{n} — n 0'dan başlar). Durum: yeni → okundu → çözüldü; yöneticinin notu.

export type FeedbackStatus = "yeni" | "okundu" | "cozuldu";
export interface FeedbackItem {
  id: number; created_at: string; message: string; status: FeedbackStatus; admin_note: string | null; demo: boolean;
  screenshots: number; user: { id: number; username: string | null } | null;
  context: { route?: string; app_version?: string; platform?: string; error?: string; code?: string; book?: string; user_agent?: string };
}
export interface FeedbackList { items: FeedbackItem[]; counts: Record<FeedbackStatus, number> }

export const FEEDBACK_KEY = ["admin-feedback"];
const LABEL: Record<FeedbackStatus, string> = { yeni: "Yeni", okundu: "Okundu", cozuldu: "Çözüldü" };
const TONE: Record<FeedbackStatus, string> = {
  yeni: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  okundu: "bg-stone-200 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  cozuldu: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
};
const shot = (id: number, n: number) => `/api/admin/feedback/${id}/screenshot/${n}`;

/** Yeni geri bildirim sayısı (Genel ve sekme rozeti). */
export const useFeedbackCounts = () => useQuery({
  queryKey: [...FEEDBACK_KEY, "all"], retry: false,
  queryFn: () => call<FeedbackList>("GET", "/api/admin/feedback?limit=1"), select: (d) => d.counts,
});

export default function AdminFeedback() {
  const [status, setStatus] = useState<FeedbackStatus | "hepsi">("yeni");
  const q = useQuery({ queryKey: [...FEEDBACK_KEY, status], retry: false,
    queryFn: () => call<FeedbackList>("GET", `/api/admin/feedback${status === "hepsi" ? "" : `?status=${status}`}`) });
  const counts = q.data?.counts;
  const [full, setFull] = useState<{ id: number; n: number } | null>(null);
  const n = (s: FeedbackStatus) => (counts ? ` ${counts[s]}` : "");
  return (
    <section className="card space-y-3 p-4" aria-labelledby="fb-h">
      <h2 id="fb-h" className="flex items-center gap-2 font-semibold"><MessageSquareWarning size={18} aria-hidden /> Geri bildirim</h2>
      <p className="text-xs text-stone-500">Uygulamadaki "Sorun bildir"den gelenler: mesaj, ekran görüntüleri ve hangi ekranda olduğu. Yeni gelince telefonuna (ve bağlıysa Telegram'a) bildirim düşer.</p>
      {/* dar ekranda alt satıra geçer: kesilmez, yatay kaydırma yok */}
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Durum">
        {([["yeni", `Yeni${n("yeni")}`], ["okundu", `Okundu${n("okundu")}`], ["cozuldu", `Çözüldü${n("cozuldu")}`], ["hepsi", "Hepsi"]] as const).map(([v, l]) => (
          <button key={v} type="button" role="radio" aria-checked={status === v} onClick={() => setStatus(v)}
            className={cx("rounded-full border px-3 py-1 text-sm", status === v ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
            {l}
          </button>
        ))}
      </div>
      <SorguDurumu q={q} etiket="Geri bildirimler yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
      {q.data && !q.data.items.length && (
        <p className="py-6 text-center text-sm text-stone-500">{status === "yeni" ? "Yeni geri bildirim yok." : "Bu durumda kayıt yok."}</p>
      )}
      <ul className="space-y-3">
        {q.data?.items.map((it) => <Row key={it.id} it={it} onShot={(i) => setFull({ id: it.id, n: i })} />)}
      </ul>
      {full && (
        <Modal title={`Geri bildirim #${full.id} · görüntü ${full.n + 1}`} onClose={() => setFull(null)}>
          <img src={shot(full.id, full.n)} alt={`Ekran görüntüsü ${full.n + 1}`} className="mx-auto max-h-[70dvh] w-auto max-w-full rounded-lg" />
          <a className="btn-ghost mt-2 inline-flex" href={shot(full.id, full.n)} target="_blank" rel="noreferrer">Yeni sekmede aç</a>
        </Modal>
      )}
    </section>
  );
}

function who(it: FeedbackItem) {
  if (it.demo) return "Demo ziyaretçisi";
  if (!it.user) return "Giriş yapmamış biri";
  return it.user.username ?? `Silinmiş hesap #${it.user.id}`;
}

function Row({ it, onShot }: { it: FeedbackItem; onShot: (n: number) => void }) {
  const ui = useUI();
  const [note, setNote] = useState<string | null>(null);
  const patch = useSave((body: { status?: FeedbackStatus; admin_note?: string }) =>
    call<FeedbackItem>("PATCH", `/api/admin/feedback/${it.id}`, body), (_r, body) => {
    setNote(null);
    ui.toast(body.status ? `${LABEL[body.status]} olarak işaretlendi` : "Not kaydedildi");
  });
  const c = it.context ?? {};
  const meta = [c.platform, c.app_version && `sürüm ${c.app_version}`, c.route, c.book === "kendi" ? "Benim defteri" : null]
    .filter(Boolean).join(" · ");
  const d = parseTs(it.created_at);
  const noteVal = note ?? it.admin_note ?? "";
  return (
    <li className="rounded-xl border border-stone-200 p-3 dark:border-stone-800">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <b className="min-w-0 [overflow-wrap:anywhere]">{who(it)}</b>
        <span className="text-xs text-stone-500">#{it.id} · {isNaN(d.getTime()) ? it.created_at : dateTimeTR(d)}</span>
        <span className={cx("ml-auto rounded px-1.5 py-0.5 text-xs font-medium", TONE[it.status])}>{LABEL[it.status]}</span>
      </div>
      <p className="mt-2 text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{it.message}</p>
      {(meta || c.error || c.code) && (
        <p className="mt-1 text-xs text-stone-500 [overflow-wrap:anywhere]">
          {meta}{c.error && <> · Hata: <span className="text-red-700 dark:text-red-400">{c.error}</span></>}{c.code && ` · kod ${c.code}`}
        </p>
      )}
      {c.user_agent && <p className="mt-0.5 truncate text-[11px] text-stone-400" title={c.user_agent}>{c.user_agent}</p>}
      {it.screenshots > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Array.from({ length: it.screenshots }, (_, i) => (
            <button key={i} type="button" onClick={() => onShot(i)} aria-label={`${i + 1}. ekran görüntüsünü büyüt`}
              className="overflow-hidden rounded-lg border border-stone-200 dark:border-stone-700">
              <img src={shot(it.id, i)} alt="" loading="lazy" className="size-20 object-cover" />
            </button>
          ))}
        </div>
      )}
      <div className="mt-3 space-y-2 border-t border-stone-100 pt-2 dark:border-stone-800">
        <label className="label" htmlFor={`fb-note-${it.id}`}>Yönetici notu</label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input id={`fb-note-${it.id}`} className="input min-w-0 flex-1" maxLength={2000} value={noteVal}
            placeholder="ör. 1.4.1 sürümünde düzeldi" onChange={(e) => setNote(e.target.value)} />
          {note !== null && note !== (it.admin_note ?? "") && (
            <button className="btn-primary shrink-0" disabled={patch.isPending} onClick={() => patch.mutate({ admin_note: note })}>Notu kaydet</button>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {(["yeni", "okundu", "cozuldu"] as const).filter((s) => s !== it.status).map((s) => (
            <button key={s} className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" disabled={patch.isPending}
              onClick={() => patch.mutate({ status: s })}>{s === "yeni" ? "Yeniye al" : `${LABEL[s]} işaretle`}</button>
          ))}
        </div>
      </div>
    </li>
  );
}
