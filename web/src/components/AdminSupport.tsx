import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, CheckCheck, FileSearch, LifeBuoy, RotateCcw, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { cx, dateTimeTR, parseTs } from "../format";
import { call } from "../lib/apiError";
import { bildirimOzeti, type SupportMessage, type SupportThread } from "../lib/destek";
import { useUI } from "../ui";
import AdminFeedback from "./AdminFeedback";
import Modal from "./Modal";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";
import { useMeta } from "./hooks";

// Yönetici: destek gelen kutusu (sunucu yeteneği "support_chat"; app/support.py). Kişi başı yazışma, okunmamış önce;
// yazışma açılınca okundu (kullanıcı "Görüldü" görür); yanıt, "Düzeltildi — tekrar dene de" (kullanıcının son ekstre
// bildirimine bağlanır), durum açık / çözüldü. uid 0: oturumsuz ve demo bildirimleri (yanıtlanamaz). Eski sunucuda
// AdminFeedback (yalnız liste) gösterilir.

type St = "acik" | "cozuldu";
interface InboxItem {
  user: { id: number; username: string | null; plan?: string };
  status: St; unread: number; updated_at: string;
  last: { author: string; body: string; created_at: string; kind: string | null };
  context: { platform?: string; app_version?: string; route?: string };
}
interface Inbox { items: InboxItem[]; counts: { acik: number; cozuldu: number; okunmamis: number }; anon: { count: number; new: number } }
interface Structure { id: number; report: Record<string, unknown>; anon: string; diag: Record<string, unknown>; cut?: boolean }

export const SUPPORT_ADMIN_KEY = ["admin-support"];
const RETRY_TEXT = "Okuyucuyu düzelttik. Bekleyen dosyanı yeniden yüklemeden tekrar deneyebilirsin.";
const zaman = (ts: string | null | undefined) => { if (!ts) return ""; const d = parseTs(ts); return isNaN(d.getTime()) ? ts : dateTimeTR(d); };

/** Okunmamış yazışma sayısı (yönetici paneli rozeti); support_chat yoksa undefined. */
export function useSupportCounts() {
  const on = !!useMeta().data?.capabilities?.includes("support_chat");
  const q = useQuery({ queryKey: [...SUPPORT_ADMIN_KEY, "inbox", null], enabled: on, retry: false,
    queryFn: () => call<Inbox>("GET", "/api/admin/support") });
  return on ? q.data?.counts : undefined;
}

export default function AdminSupport() {
  const on = !!useMeta().data?.capabilities?.includes("support_chat");
  const [open, setOpen] = useState<number | null>(null);
  if (!on) return <AdminFeedback />;
  return open != null ? <ThreadView uid={open} onBack={() => setOpen(null)} /> : <InboxView onOpen={setOpen} />;
}

function InboxView({ onOpen }: { onOpen: (uid: number) => void }) {
  const [st, setSt] = useState<St | "hepsi">("acik");
  const q = useQuery({ queryKey: [...SUPPORT_ADMIN_KEY, "inbox", st === "hepsi" ? null : st], retry: false,
    queryFn: () => call<Inbox>("GET", `/api/admin/support${st === "hepsi" ? "" : `?status=${st}`}`) });
  const c = q.data?.counts;
  return (
    <section className="card space-y-3 p-4" aria-labelledby="sup-h">
      <h2 id="sup-h" className="flex items-center gap-2 font-semibold"><LifeBuoy size={18} aria-hidden /> Destek gelen kutusu
        {!!c?.okunmamis && <span className="rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold text-white tabular-nums">{c.okunmamis}</span>}
      </h2>
      <p className="text-xs text-stone-500">Kullanıcıların yazışmaları ve ekstre bildirimleri. Yanıtın kullanıcıya bildirim olarak gider; yazışmayı açınca "Görüldü" görür.</p>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Durum">
        {([["acik", `Açık${c ? ` ${c.acik}` : ""}`], ["cozuldu", `Çözüldü${c ? ` ${c.cozuldu}` : ""}`], ["hepsi", "Hepsi"]] as const).map(([v, l]) => (
          <button key={v} type="button" role="radio" aria-checked={st === v} onClick={() => setSt(v)}
            className={cx("rounded-full border px-3 py-1 text-sm", st === v ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>{l}</button>
        ))}
      </div>
      <SorguDurumu q={q} etiket="Gelen kutusu yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
      {q.data && !q.data.items.length && <p className="py-6 text-center text-sm text-stone-500">Bu durumda yazışma yok.</p>}
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {q.data?.items.map((it) => (
          <li key={it.user.id}>
            <button type="button" className="flex w-full min-w-0 items-start gap-3 py-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/40" onClick={() => onOpen(it.user.id)}>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2">
                  <b className={cx("min-w-0 [overflow-wrap:anywhere]", !it.unread && "font-medium")}>{it.user.username ?? `Silinmiş hesap #${it.user.id}`}</b>
                  {it.status === "cozuldu" && <span className="rounded bg-green-100 px-1.5 text-[11px] text-green-800 dark:bg-green-950 dark:text-green-300">Çözüldü</span>}
                </span>
                <span className="mt-0.5 block truncate text-sm text-stone-600 dark:text-stone-400">
                  {it.last.author === "admin" ? "Sen: " : it.last.author === "system" ? "Sistem: " : ""}{it.last.body.split("\n")[0]}
                </span>
                <span className="block text-xs text-stone-500">{[it.context.platform, it.context.app_version, it.context.route].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="flex shrink-0 flex-col items-end gap-1">
                <span className="text-xs text-stone-500 tabular-nums">{zaman(it.updated_at)}</span>
                {!!it.unread && <span className="rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold text-white tabular-nums" aria-label={`${it.unread} okunmamış`}>{it.unread}</span>}
              </span>
            </button>
          </li>
        ))}
        {!!q.data?.anon.count && (
          <li>
            <button type="button" className="flex w-full items-center gap-3 py-3 text-left text-sm hover:bg-stone-50 dark:hover:bg-stone-800/40" onClick={() => onOpen(0)}>
              <span className="min-w-0 flex-1 text-stone-600 dark:text-stone-400">Giriş yapmamış / demo · {q.data.anon.count}</span>
              {!!q.data.anon.new && <span className="rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold text-white">{q.data.anon.new}</span>}
            </button>
          </li>
        )}
      </ul>
    </section>
  );
}

function ThreadView({ uid, onBack }: { uid: number; onBack: () => void }) {
  const qc = useQueryClient();
  const ui = useUI();
  const q = useQuery({ queryKey: [...SUPPORT_ADMIN_KEY, "thread", uid], retry: false,
    queryFn: () => call<SupportThread>("GET", `/api/admin/support/${uid}`) });
  const unread = q.data?.thread.admin_unread ?? 0;
  useEffect(() => {
    if (!unread) return;
    void call("POST", `/api/admin/support/${uid}/seen`).then(() => qc.invalidateQueries({ queryKey: SUPPORT_ADMIN_KEY }));
  }, [unread, uid, qc]);
  const [text, setText] = useState("");
  const [resolve, setResolve] = useState(false);
  const [retry, setRetry] = useState<number | null>(null);
  const [struct, setStruct] = useState<number | null>(null);
  const lastReport = [...(q.data?.messages ?? [])].reverse().find((m) => m.kind === "ekstre" && m.author === "user");
  const reply = useMutation({
    mutationFn: () => call("POST", `/api/admin/support/${uid}/reply`, { message: text.trim(), resolve, retry_ref: retry }),
    onSuccess: () => { setText(""); setRetry(null); setResolve(false); ui.toast("Yanıt gönderildi"); void qc.invalidateQueries({ queryKey: SUPPORT_ADMIN_KEY }); },
    onError: (e) => ui.error((e as Error).message),
  });
  const status = useMutation({
    mutationFn: (s: St) => call("PATCH", `/api/admin/support/${uid}`, { status: s }),
    onSuccess: (_r, s) => { ui.toast(s === "cozuldu" ? "Çözüldü olarak işaretlendi" : "Yeniden açıldı"); void qc.invalidateQueries({ queryKey: SUPPORT_ADMIN_KEY }); },
    onError: (e) => ui.error((e as Error).message),
  });
  const st = q.data?.thread.status;
  const name = uid === 0 ? "Giriş yapmamış / demo" : q.data?.user?.username ?? `#${uid}`;
  return (
    <section className="card space-y-3 p-4" aria-labelledby="th-h">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost -ml-2 px-2" onClick={onBack}><ChevronLeft size={16} aria-hidden />Gelen kutusu</button>
        <h2 id="th-h" className="order-last w-full min-w-0 font-semibold [overflow-wrap:anywhere] sm:order-none sm:w-auto sm:flex-1">{name}</h2>
        <div className="flex-1 sm:hidden" />
        {uid !== 0 && st && (
          <button type="button" className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" disabled={status.isPending}
            onClick={() => status.mutate(st === "cozuldu" ? "acik" : "cozuldu")}>{st === "cozuldu" ? "Yeniden aç" : "Çözüldü işaretle"}</button>
        )}
      </div>
      <SorguDurumu q={q} etiket="Yazışma yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
      <ul className="space-y-3">
        {q.data?.messages.map((m) => <AdminMessage key={m.id} m={m} onStruct={() => setStruct(m.id)} />)}
      </ul>
      {uid === 0 ? <p className="text-xs text-stone-500">Bu bildirimler hesaba bağlı değil; yanıtlanamaz.</p> : (
        <div className="space-y-2 border-t border-stone-100 pt-3 dark:border-stone-800">
          <label className="label" htmlFor="sup-reply">Yanıt yaz</label>
          <textarea id="sup-reply" className="input min-h-24" maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
          {retry != null && <p className="text-xs text-blue-700 dark:text-blue-400">"Tekrar dene" düğmesi kullanıcının son ekstre bildirimine bağlanacak.
            <button type="button" className="ml-1 underline" onClick={() => setRetry(null)}>Kaldır</button></p>}
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={resolve} onChange={(e) => setResolve(e.target.checked)} />Çözüldü olarak kapat</label>
            <div className="flex-1" />
            {lastReport && retry == null && (
              <button type="button" className="btn-ghost border border-stone-300 text-sm dark:border-stone-700"
                onClick={() => { setRetry(lastReport.id); setText((t) => t.trim() ? t : RETRY_TEXT); }}><RotateCcw size={15} aria-hidden />Düzeltildi — tekrar dene de</button>
            )}
            <button type="button" className="btn-primary" disabled={!text.trim() || reply.isPending} onClick={() => reply.mutate()}><Send size={15} aria-hidden />Yanıtla</button>
          </div>
        </div>
      )}
      {struct != null && <StructureModal id={struct} onClose={() => setStruct(null)} />}
    </section>
  );
}

function AdminMessage({ m, onStruct }: { m: SupportMessage; onStruct: () => void }) {
  if (m.author === "system") return <li className="text-center text-xs text-stone-500">{m.body} · {zaman(m.created_at)}</li>;
  const user = m.author === "user";
  const c = m.context ?? {};
  const meta = [c.platform, c.app_version && `sürüm ${c.app_version}`, c.route].filter(Boolean).join(" · ");
  return (
    <li className={cx("flex", user ? "justify-start" : "justify-end")}>
      <div className={cx("max-w-[90%] min-w-0 rounded-2xl px-3 py-2 text-sm",
        user ? "rounded-bl-md bg-stone-100 dark:bg-stone-800" : "rounded-br-md bg-blue-600 text-white")}>
        {m.kind === "ekstre" && m.report && (
          <div className="mb-1 text-xs text-stone-500"><b className="text-stone-800 dark:text-stone-200">Ekstre bildirimi</b> · {bildirimOzeti(m.report)}
            {m.report.sent_doc ? " · yapı eklendi" : " · belge yok"}</div>
        )}
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{m.body}</p>
        {user && (meta || c.error) && (
          <p className="mt-1 text-xs text-stone-500 [overflow-wrap:anywhere]">{meta}{c.error && <> · Hata: <span className="text-red-700 dark:text-red-400">{c.error}</span></>}{c.code ? ` · kod ${c.code}` : ""}</p>
        )}
        {m.screenshots > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {Array.from({ length: m.screenshots }, (_, i) => (
              <a key={i} href={`/api/admin/feedback/${m.id}/screenshot/${i}`} target="_blank" rel="noreferrer" aria-label={`${i + 1}. ekran görüntüsünü aç`}
                className="overflow-hidden rounded-lg border border-stone-200 dark:border-stone-700"><img src={`/api/admin/feedback/${m.id}/screenshot/${i}`} alt="" loading="lazy" className="size-16 object-cover" /></a>
            ))}
          </div>
        )}
        {m.structure && (
          <button type="button" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={onStruct}>
            <FileSearch size={14} aria-hidden />Maskeli yapıyı gör</button>
        )}
        {m.action?.type === "retry" && <p className="mt-1 text-xs text-blue-100">"Tekrar dene" eklendi{m.action.done ? " · kullanıcı okuttu" : ""}</p>}
        <div className={cx("mt-1 flex items-center justify-end gap-1 text-[11px]", user ? "text-stone-500" : "text-blue-100")}>
          {m.demo && "demo · "}{zaman(m.created_at)}{user && m.seen && <CheckCheck size={13} aria-label="okundu" />}
        </div>
      </div>
    </li>
  );
}

function StructureModal({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: [...SUPPORT_ADMIN_KEY, "structure", id], retry: false,
    queryFn: () => call<Structure>("GET", `/api/admin/support/messages/${id}/structure`) });
  return (
    <Modal title={`Maskeli yapı · #${id}`} onClose={onClose}>
      <SorguDurumu q={q} etiket="Yapı yükleniyor"><SatirIskelet n={4} /></SorguDurumu>
      {q.data && (
        <div className="space-y-3">
          <p className="text-xs text-stone-500">Dosyanın kendisi değil: isimler, açıklamalar, numaralar ve tutarların rakamları maskeli. Yalnız etiketler ve tarih/tutar düzeni.</p>
          <div>
            <div className="label">Teşhis</div>
            <pre className="max-h-48 overflow-auto rounded-lg bg-stone-100 p-2 text-xs dark:bg-stone-800">{JSON.stringify(q.data.diag, null, 2)}</pre>
          </div>
          <div>
            <div className="label">Maskeli metin{q.data.cut ? " (kısaltıldı)" : ""}</div>
            <pre className="max-h-[50dvh] overflow-auto rounded-lg bg-stone-100 p-2 font-mono text-xs leading-snug whitespace-pre dark:bg-stone-800">{q.data.anon || "(metin yok)"}</pre>
          </div>
        </div>
      )}
    </Modal>
  );
}
