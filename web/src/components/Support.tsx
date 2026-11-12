import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, CheckCircle2, ImagePlus, LifeBuoy, RotateCcw, Send, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cx, dateTimeTR, dayMonthTR, parseTs } from "../format";
import {
  BEKLEME_GUN, CODES, GORSEL_EN_COK, MESAJ_EN_COK, baglamMetni, bildirimOzeti, destekApi, destekKapat, dosyaMetni,
  useDestekDurumu, type ReportedUpload, type SupportMessage, type YardimBaglami,
} from "../lib/destek";
import { useUI } from "../ui";
import FeedbackDialog from "./Feedback";
import Modal from "./Modal";
import { useMeta } from "./hooks";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

// Destek yazışması (sunucu yeteneği "support_chat"; lib/destek.ts). Kök: App.tsx (SupportHost) — "Diğer → Destek",
// yükleme hata satırının "Bildir"i sonrası ve her hata ekranındaki yardimAl() aynı pencereyi açar.

export const SUPPORT_KEY = ["support"];
export const UNREAD_KEY = ["support-unread"];
const TYPES = ["image/jpeg", "image/png", "image/webp"];

/** Sunucu yeteneği (eski sunucuda false). */
export function useCap(c: string): boolean {
  return !!useMeta().data?.capabilities?.includes(c);
}

/** Okunmamış yönetici yanıtı (rozet). */
export function useSupportUnread() {
  const on = useCap("support_chat");
  const q = useQuery({ queryKey: UNREAD_KEY, queryFn: destekApi.unread, enabled: on, retry: false, refetchInterval: 120_000 });
  return on ? q.data?.unread ?? 0 : 0;
}

const zaman = (ts: string) => { const d = parseTs(ts); return isNaN(d.getTime()) ? ts : dateTimeTR(d); };
const gun = (ts: string) => dayMonthTR(ts.slice(0, 10));

/** App.tsx'te bir kez: Destek açıksa çizer. support_chat yoksa "Sorun bildir" (bağlamla). */
export function SupportHost() {
  const d = useDestekDurumu();
  const on = useCap("support_chat");
  if (!d.open) return null;
  if (!on) {
    const b = d.baglam;
    return <FeedbackDialog onClose={destekKapat} context={b ? { error: [b.nerede, b.ayrinti].filter(Boolean).join(": "), code: b.kod != null ? String(b.kod) : undefined } : undefined} />;
  }
  return <SupportDialog key={d.no} baglam={d.baglam} onClose={destekKapat} />;
}

function SupportDialog({ baglam: ilk, onClose }: { baglam: YardimBaglami | null; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: SUPPORT_KEY, queryFn: destekApi.thread, retry: false });
  const [baglam, setBaglam] = useState<YardimBaglami | null>(ilk);
  const unread = q.data?.thread.unread ?? 0;
  useEffect(() => {
    if (!unread) return;
    void destekApi.seen().then(() => qc.invalidateQueries({ queryKey: UNREAD_KEY }));
  }, [unread, qc]);
  const end = useRef<HTMLDivElement>(null);
  const n = q.data?.messages.length ?? 0;
  useEffect(() => { if (n) end.current?.scrollIntoView({ block: "end" }); }, [n]);
  return (
    <Modal title="Destek" onClose={onClose} footer={<Composer baglam={baglam} onClearBaglam={() => setBaglam(null)} />}>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-400">Bize yaz; yanıtımız burada ve bildirim olarak gelir.</p>
        {q.data?.thread.status === "cozuldu" && n > 0 && (
          <p className="flex items-center gap-2 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            <CheckCircle2 size={16} aria-hidden className="shrink-0" />Çözüldü olarak kapatıldı — yazarsan yeniden açılır.</p>
        )}
        <SorguDurumu q={q} etiket="Yazışma yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
        {q.data && !n && <p className="py-8 text-center text-sm text-stone-500">Henüz yazışma yok. Bir sorun ya da önerin varsa yaz.</p>}
        <ul className="space-y-3" aria-label="Destek yazışması">
          {q.data?.messages.map((m) => <Message key={m.id} m={m} linked={m.action ? q.data.messages.find((x) => x.id === m.action!.ref) : undefined} />)}
        </ul>
        <div ref={end} />
      </div>
    </Modal>
  );
}

function Message({ m, linked }: { m: SupportMessage; linked?: SupportMessage }) {
  if (m.author === "system") {
    return <li className="text-center text-xs text-stone-500 dark:text-stone-400">{m.body} · {zaman(m.created_at)}</li>;
  }
  const mine = m.author === "user";
  return (
    <li className={cx("flex", mine ? "justify-end" : "justify-start")}>
      <div className={cx("max-w-[85%] min-w-0 rounded-2xl px-3 py-2 text-sm",
        mine ? "rounded-br-md bg-blue-600 text-white" : "rounded-bl-md bg-stone-100 text-stone-900 dark:bg-stone-800 dark:text-stone-100")}>
        {!mine && <div className="mb-0.5 text-xs font-semibold text-blue-700 dark:text-blue-300">HesapKitap Destek</div>}
        {m.kind === "ekstre" && m.report && (
          <div className={cx("mb-1 text-xs", mine ? "text-blue-100" : "text-stone-500")}>
            <b className={mine ? "text-white" : ""}>Ekstre bildirimi</b>
            <span className="block [overflow-wrap:anywhere]">{bildirimOzeti(m.report)}</span>
            <span className="block">{m.report.sent_doc ? "Maskeli yapı gönderildi" : "Belge gönderilmedi"}</span>
          </div>
        )}
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{m.body}</p>
        {mine && m.context?.error && m.kind !== "ekstre" && (
          <p className="mt-1 text-xs text-blue-100 [overflow-wrap:anywhere]">Bağlam: {m.context.error}{m.context.code ? ` · kod ${m.context.code}` : ""}</p>
        )}
        {m.screenshots > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {Array.from({ length: m.screenshots }, (_, i) => (
              <a key={i} href={destekApi.shot(m.id, i)} target="_blank" rel="noreferrer" aria-label={`${i + 1}. görüntüyü aç`}
                className="overflow-hidden rounded-lg border border-white/30"><img src={destekApi.shot(m.id, i)} alt="" loading="lazy" className="size-16 object-cover" /></a>
            ))}
          </div>
        )}
        {m.kind === "ekstre" && mine && <PendingLine p={m.pending ?? null} />}
        {m.action?.type === "retry" && linked?.report && (
          <p className="mt-1 text-xs text-stone-500 [overflow-wrap:anywhere]">Ekstre bildirimi · {bildirimOzeti(linked.report)}</p>
        )}
        {m.action?.type === "retry" && <RetryBox a={m.action} />}
        <div className={cx("mt-1 flex items-center justify-end gap-1 text-[11px]", mine ? "text-blue-100" : "text-stone-500")}>
          {zaman(m.created_at)}
          {mine && (m.seen ? <> · <CheckCheck size={13} aria-hidden />Görüldü</> : " · Gönderildi")}
        </div>
      </div>
    </li>
  );
}

function PendingLine({ p }: { p: SupportMessage["pending"] }) {
  const qc = useQueryClient();
  const ui = useUI();
  const drop = useMutation({
    mutationFn: (id: number) => destekApi.dropPending(id),
    onSuccess: () => { ui.toast("Dosya silindi"); void qc.invalidateQueries({ queryKey: SUPPORT_KEY }); },
    onError: (e) => ui.error((e as Error).message),
  });
  if (!p) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-white/15 px-2 py-1.5 text-xs">
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">Dosya {BEKLEME_GUN} gün bekliyor · son gün {gun(p.expires_at)}</span>
      <button type="button" className="inline-flex items-center gap-1 rounded-md border border-white/40 px-2 py-1 font-medium"
        disabled={drop.isPending} onClick={() => drop.mutate(p.id)}><Trash2 size={13} aria-hidden />Dosyayı sil</button>
    </div>
  );
}

function RetryBox({ a }: { a: NonNullable<SupportMessage["action"]> }) {
  const qc = useQueryClient();
  const ui = useUI();
  const [msg, setMsg] = useState<string | null>(null);
  const [needPw, setNeedPw] = useState(false);
  const [pw, setPw] = useState("");
  const run = useMutation({
    mutationFn: (password?: string) => destekApi.retry(a.pending!.id, password),
    onSuccess: (r) => {
      if (r.done) {
        setMsg(null); setNeedPw(false);
        ui.toast("Ekstre okundu ve kaydedildi");
        void qc.invalidateQueries();
        return;
      }
      if (r.status === "password_required") { setNeedPw(true); setMsg(r.message || "PDF şifreli"); return; }
      setMsg(r.message || "Okunamadı");
      void qc.invalidateQueries({ queryKey: SUPPORT_KEY });
    },
    onError: (e) => setMsg((e as Error).message),
  });
  if (a.done) return <p className="mt-2 flex items-center gap-1 text-xs font-medium text-green-700 dark:text-green-400"><CheckCircle2 size={14} aria-hidden />Okundu</p>;
  if (!a.pending) return <p className="mt-2 text-xs text-stone-500">Dosya artık yok — ekstreyi yeniden yükle</p>;
  return (
    <div className="mt-2 space-y-2">
      <button type="button" className="btn-primary px-3 py-1.5 text-sm" disabled={run.isPending} onClick={() => run.mutate(undefined)}>
        <RotateCcw size={15} aria-hidden />{run.isPending ? "Okunuyor…" : "Tekrar dene"}</button>
      {needPw && (
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (pw) run.mutate(pw); }}>
          <label className="sr-only" htmlFor={`pw-${a.ref}`}>PDF şifresi</label>
          <input id={`pw-${a.ref}`} type="password" autoComplete="off" className="input min-w-0 flex-1" placeholder="PDF şifresi" value={pw} onChange={(e) => setPw(e.target.value)} />
          <button className="btn-primary shrink-0" disabled={!pw || run.isPending}>Aç ve oku</button>
        </form>
      )}
      {msg && <p className="text-xs text-red-700 dark:text-red-400 [overflow-wrap:anywhere]" role="alert">{msg}</p>}
    </div>
  );
}

function Composer({ baglam, onClearBaglam }: { baglam: YardimBaglami | null; onClearBaglam: () => void }) {
  const qc = useQueryClient();
  const ui = useUI();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const urls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);
  const send = useMutation({
    mutationFn: () => destekApi.send(text, files, {
      route: window.location.hash || "#/", platform: "web",
      error: baglam ? [baglam.nerede, baglam.ayrinti].filter(Boolean).join(": ") : undefined,
      code: baglam?.kod != null ? String(baglam.kod) : undefined,
    }),
    onSuccess: () => {
      setText(""); setFiles([]); setErr(null); onClearBaglam();
      ui.toast("Gönderildi");
      void qc.invalidateQueries({ queryKey: SUPPORT_KEY });
    },
    onError: (e) => setErr((e as Error).message),
  });
  const add = (list: FileList | null) => {
    setErr(null);
    const picked = Array.from(list ?? []).filter((f) => TYPES.includes(f.type) && f.size <= 5 * 1024 * 1024);
    if (picked.length < (list?.length ?? 0)) setErr("Yalnız JPEG, PNG ya da WebP (en çok 5 MB) eklenebilir");
    setFiles((x) => [...x, ...picked].slice(0, GORSEL_EN_COK));
  };
  return (
    <div className="w-full min-w-0 space-y-2">
      {baglam && (
        <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-stone-100 py-0.5 pr-1 pl-2.5 text-xs text-stone-700 dark:bg-stone-800 dark:text-stone-300">
          <span className="min-w-0 truncate">{baglamMetni(baglam)}</span>
          <button type="button" aria-label="Bağlamı kaldır" className="rounded-full p-1 hover:bg-stone-200 dark:hover:bg-stone-700" onClick={onClearBaglam}><X size={12} aria-hidden /></button>
        </span>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {files.map((f, i) => (
            <div key={`${f.name}-${i}`} className="relative">
              <img src={urls[i]} alt={`Fotoğraf ${i + 1}`} className="size-14 rounded-lg border border-stone-200 object-cover dark:border-stone-700" />
              <button type="button" aria-label={`${i + 1}. fotoğrafı çıkar`} onClick={() => setFiles(files.filter((_, j) => j !== i))}
                className="absolute -top-2 -right-2 flex size-6 items-center justify-center rounded-full bg-stone-800 text-white dark:bg-stone-200 dark:text-stone-900"><X size={13} aria-hidden /></button>
            </div>
          ))}
        </div>
      )}
      <label className="sr-only" htmlFor="destek-msg">Mesajını yaz</label>
      <textarea id="destek-msg" className="input min-h-20 w-full" maxLength={MESAJ_EN_COK} placeholder="Mesajını yaz" value={text}
        onChange={(e) => setText(e.target.value)} />
      {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {files.length < GORSEL_EN_COK && (
          <button type="button" className="btn-ghost text-sm" onClick={() => input.current?.click()}><ImagePlus size={16} aria-hidden />Fotoğraf ekle</button>
        )}
        <input ref={input} type="file" accept={TYPES.join(",")} multiple className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        <div className="flex-1" />
        <button type="button" className="btn-primary" disabled={!text.trim() || send.isPending} onClick={() => send.mutate()}>
          <Send size={16} aria-hidden />{send.isPending ? "Gönderiliyor…" : "Gönder"}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- yükleme hata satırı: "Bildir"

/** "Ekstreyi bildir" penceresi. file: satırın dosyası (yalnız onay açıksa gönderilir). */
export function ReportDialog({ row, file, onClose, onDone }: {
  row: ReportedUpload; file: File | null; onClose: () => void; onDone: () => void;
}) {
  const ui = useUI();
  const canSend = useCap("upload_retry") && !!file;
  const rep = row.report!;
  const [bank, setBank] = useState(rep.bank_guess ?? "");
  const [note, setNote] = useState("");
  const [sendDoc, setSendDoc] = useState(false); // VARSAYILAN KAPALI: açık onay
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const go = useMutation({
    mutationFn: () => destekApi.report(rep, bank, note, sendDoc && canSend ? file : null),
    onSuccess: () => {
      ui.toast("Bildirildi. Yanıtı Ayarlar → Destek'te görürsün.");
      void qc.invalidateQueries({ queryKey: SUPPORT_KEY });
      onDone();
    },
    onError: (e) => setErr((e as Error).message),
  });
  const rows: [string, string][] = [
    ["Neden", CODES[rep.code] ?? CODES.okunamadi!],
    ["Dosya", dosyaMetni(rep)],
    ["Tanınan banka", rep.bank_guess || "Tanınmadı"],
    ["Uygulama", "Web"],
  ];
  return (
    <Modal title="Ekstreyi bildir" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button type="button" className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button type="button" className="btn-primary" disabled={go.isPending} onClick={() => go.mutate()}>{go.isPending ? "Gönderiliyor…" : "Gönder"}</button>
    </>}>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-400">Bu belgeyi okuyamadık. Bize bildir, okuyucuyu düzeltince sana haber verelim.</p>
        <dl className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-800">
          {rows.map(([k, v]) => (
            <div key={k} className="flex gap-3 px-3 py-2">
              <dt className="w-32 shrink-0 text-stone-500">{k}</dt><dd className="min-w-0 flex-1 [overflow-wrap:anywhere]">{v}</dd>
            </div>
          ))}
        </dl>
        <div>
          <label className="label" htmlFor="rep-bank">Banka adı</label>
          <input id="rep-bank" className="input" maxLength={80} placeholder="ör. Ornek Bank" value={bank} onChange={(e) => setBank(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="rep-note">Not (isteğe bağlı)</label>
          <textarea id="rep-note" className="input min-h-20" maxLength={1000} placeholder="ör. Kredi kartı ekstresi, bankanın sitesinden indirdim"
            value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {canSend && (
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-stone-200 p-3 dark:border-stone-800">
            <input type="checkbox" role="switch" aria-checked={sendDoc} className="mt-0.5 size-5 shrink-0" checked={sendDoc} onChange={(e) => setSendDoc(e.target.checked)} />
            <span className="text-sm font-medium">Belgeyi bize gönder — okuyucuyu düzeltip sana haber verelim</span>
          </label>
        )}
        <p className="text-xs text-stone-500 dark:text-stone-400">
          {canSend
            ? "Açarsan dosyanın kendisi bize gelmez: isimler, açıklamalar, kart ve hesap numaraları ve tutarlar maskelenmiş bir iskelet gider (yalnız etiketler ve tarih/tutar düzeni). Dosya 30 gün yalnız senin defterinde bekler; düzeltilince yeniden yüklemeden tekrar deneyebilirsin. Kapalıysa yalnız yukarıdaki bilgiler gider."
            : "Yalnız yukarıdaki bilgiler gider; dosyanın kendisi gönderilmez."}
        </p>
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}

/** Satırdaki düğme: "Bildir" ya da gönderildiyse "Bildirildi". */
export function ReportButton({ row, file }: { row: ReportedUpload; file: File | null }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  if (done) return <span className="inline-flex items-center gap-1 rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-950 dark:text-green-300"><CheckCircle2 size={12} aria-hidden />Bildirildi</span>;
  return (
    <>
      <button type="button" className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setOpen(true)}>
        <LifeBuoy size={14} aria-hidden />Bildir</button>
      {open && <ReportDialog row={row} file={file} onClose={() => setOpen(false)} onDone={() => { setOpen(false); setDone(true); }} />}
    </>
  );
}
