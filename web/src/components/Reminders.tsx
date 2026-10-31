import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BellOff, Check, Clock, CreditCard, FileText, GitMerge, HandCoins, Landmark, Link2, MoreHorizontal, Pencil, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errStatus, movedCount } from "../api";
import { cx, dateTR, tl } from "../format";
import { useUI, type Route } from "../ui";
import { useOpenRow, useSave } from "./hooks";
import { bodyOf, call } from "../lib/apiError";
import { PairCards } from "./MatchInfo";

/** /api/reminders: kararlı id ("ekstre:<kart>:<YYYY-MM>"), aynı veri ileride push bildirimlerinde de kullanılır. */
export interface Reminder {
  id: string; kind: "ekstre" | "dokum" | "odeme" | "esles" | "hesap" | "kart_eslesmedi" | "borc_odeme"; title: string; text: string; cta: string; route: Route;
  action?: "upload" | "pay" | "match" | "account" | "card_match" | "debt_pay"; due: string; severity: "info" | "warn" | "urgent"; tag?: string; bank?: string; last4?: string;
  statement_id?: number;
  // esles: "Bu ikisi aynı mı?"
  entry_id?: number; other?: string; reason?: "tarih" | "tutar" | "aday"; source?: "dokum" | "ekstre";
  pair?: {
    entry: { id: number; uid: string; description: string; amount: number; date: string; kind: string; category: string | null;
      note: string | null; account: string | null; created_by: number | null; receipt: boolean };
    other: { uid: string; id: number; description: string; amount: number; date: string; account: string | null; source: "dokum" | "ekstre" };
  };
  // hesap: "Bu döküm “Garanti” hesabına mı ait?"
  account_id?: number; into?: number; into_name?: string;
  // borc_odeme: "Bu havale borçtan mı?" — evet: POST /api/debts/{debt_id}/pay {pay}
  debt_id?: number; pay?: { amount: number; paid_at: string; entry_id: number };
}

const post = (id: string, what: "snooze" | "dismiss", body?: object) =>
  call("POST", `/api/reminders/${encodeURIComponent(id)}/${what}`, body);

// banka rozeti: logo uydurmadan, adın baş harfi + addan türeyen sabit bir ton
const TONES = [
  "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
];
const tone = (s: string) => TONES[[...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TONES.length];
const TAG = {
  info: "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  urgent: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

function Badge({ r }: { r: Reminder }) {
  const bank = r.bank ?? r.pair?.other.account ?? "";
  const Ic = r.kind === "esles" ? Link2 : r.kind === "hesap" ? GitMerge : r.kind === "dokum" ? Landmark : r.kind === "odeme" ? Clock
    : r.kind === "borc_odeme" ? HandCoins : CreditCard;
  return (
    <span className={cx("relative flex h-11 w-[52px] shrink-0 flex-col justify-between rounded-lg px-1.5 py-1 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.04)]", tone(bank))} aria-hidden="true">
      <span className="flex items-center justify-between">
        {bank ? <>
          <span className="font-display text-sm leading-none font-bold">{bank.slice(0, 1).toLocaleUpperCase("tr")}</span>
          <Ic size={12} className="opacity-70" />
        </> : <Ic size={15} className="opacity-80" />}
      </span>
      <span className="text-[10px] leading-none font-semibold tracking-wider tabular-nums opacity-80">{r.last4 ? `••${r.last4}` : r.kind === "esles" ? (r.source === "ekstre" ? "Ekstre" : "Döküm")
        : r.kind === "kart_eslesmedi" ? "Kart" : r.kind === "borc_odeme" ? "Borç" : "Hesap"}</span>
    </span>
  );
}

function Menu({ r, onDone }: { r: Reminder; onDone: (msg: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const snooze = useSave(() => post(r.id, "snooze", { days: 1 }), () => onDone("Yarın yeniden hatırlatırız"));
  const dismiss = useSave(() => post(r.id, "dismiss"), () => onDone("Bu dönem için gizlendi"));
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const item = "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800";
  return (
    <div ref={ref} className="relative">
      <button className="btn-ghost p-1.5 text-stone-400" aria-label="Diğer seçenekler" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}><MoreHorizontal size={16} /></button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-stone-200 bg-white p-1 shadow-lg dark:border-stone-700 dark:bg-stone-900">
          <button role="menuitem" className={item} onClick={() => { setOpen(false); snooze.mutate(undefined); }}>
            <Clock size={14} className="text-stone-400" />Yarın hatırlat</button>
          <button role="menuitem" className={item} onClick={() => { setOpen(false); dismiss.mutate(undefined); }}>
            <BellOff size={14} className="text-stone-400" />Bu ay gösterme</button>
        </div>
      )}
    </div>
  );
}

/** "Bu ikisi aynı mı?" ve "Bu döküm X hesabına mı ait?" satırlarının cevap düğmeleri. */
function useAnswers() {
  const ui = useUI();
  const qc = useQueryClient();
  const stale = (e: Error) => {
    ui.error(errStatus(e) === 409 ? "Bu soru artık geçerli değil" : e.message);
    void qc.invalidateQueries({ queryKey: ["reminders"] });
  };
  const match = useSave(({ r, same }: { r: Reminder; same: boolean }) => api.matchEntry(r.entry_id!, r.other!, same), (res) => {
    if (!res.same) return ui.toast("Tamam, ikisi ayrı kayıt olarak kalır; bir daha sormayız");
    const more = res.auto_merged ? ` · ${res.auto_merged} kayıt daha kendiliğinden eşleşti` : "";
    ui.toast(res.learned
      ? `Öğrendim: bundan sonra ${res.learned.bank} ↔ ${res.learned.entry} kendiliğinden eşleşir${more}`
      : `Birleştirildi · iki kez sayılmaz${more}`);
  });
  const merge = useSave((r: Reminder) => api.mergeAccount(r.account_id!, r.into!), (res) =>
    ui.toast(`Hesaplar birleşti · ${movedCount(res.moved)} kayıt taşındı${res.matched ? ` · ${res.matched} kayıt eşleşti` : ""}`));
  const separate = useSave((r: Reminder) => api.separateAccount(r.account_id!, r.into!), () =>
    ui.toast("Tamam, ayrı hesaplar olarak kalır"));
  return {
    busy: match.isPending || merge.isPending || separate.isPending,
    same: (r: Reminder, same: boolean) => match.mutate({ r, same }, { onError: stale }),
    merge: (r: Reminder) => merge.mutate(r, { onError: stale }),
    separate: (r: Reminder) => separate.mutate(r, { onError: stale }),
  };
}

function Question({ r, a }: { r: Reminder; a: ReturnType<typeof useAnswers> }) {
  const ui = useUI();
  const titleId = `rem-${r.id.replace(/[^a-z0-9]/gi, "-")}`;
  return (
    <li className="px-2 py-3 sm:px-3" aria-labelledby={titleId}>
      <div className="flex items-start gap-3">
        <Badge r={r} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span id={titleId} className="break-words text-sm font-semibold">{r.title}</span>
            {r.tag && <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", TAG[r.severity])}>{r.tag}</span>}
          </div>
          {r.kind === "esles" && r.pair ? (
            <>
              <p className="sr-only">{r.text}</p>
              <PairCards entry={r.pair.entry} other={r.pair.other} source={r.source ?? r.pair.other.source} reason={r.reason ?? "tarih"} />
              {r.reason === "aday" && (
                <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
                  {r.source === "ekstre" ? "Ekstrede" : "Dökümde"} buna benzeyen başka satır da var; doğru olan bu değilse “Farklı” de, sıradakini sorarız.</p>
              )}
            </>
          ) : (
            <p className="text-xs text-stone-500 dark:text-stone-400">{r.text}</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {r.kind === "esles" ? <>
              <button className="btn-primary px-3" disabled={a.busy} onClick={() => a.same(r, true)}><Check size={14} />Aynı</button>
              <button className="btn-ghost border border-stone-200 px-3 dark:border-stone-700" disabled={a.busy} onClick={() => a.same(r, false)}>
                <X size={14} />Farklı</button>
            </> : <>
              <button className="btn-primary px-3" disabled={a.busy} onClick={() => a.merge(r)}><GitMerge size={14} />{r.cta || "Evet, birleştir"}</button>
              <button className="btn-ghost border border-stone-200 px-3 dark:border-stone-700" disabled={a.busy} onClick={() => a.separate(r)}>Ayrı hesap</button>
              <button className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={() => ui.go("hesaplar")}>Hesaplara bak</button>
            </>}
          </div>
        </div>
        <Menu r={r} onDone={ui.toast} />
      </div>
    </li>
  );
}

/** Satır başlığı + metin (soru satırları ortak). */
function Head({ r }: { r: Reminder }) {
  return (<>
    <div className="flex items-center gap-2">
      <span className="text-sm font-semibold break-words">{r.title}</span>
      {r.tag && <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", TAG[r.severity])}>{r.tag}</span>}
    </div>
    <p className="text-xs break-words text-stone-500 dark:text-stone-400">{r.text}</p>
  </>);
}

/** Sorunun cevabı başarısızsa: 404 kayıt yok, 409 soru geçersiz, 422 sunucunun Türkçe nedeni. Sorular tazelenir. */
function useAnswerError() {
  const ui = useUI();
  const qc = useQueryClient();
  return (e: Error) => {
    const s = errStatus(e);
    ui.error(s === 409 ? "Bu soru artık geçerli değil" : s === 404 ? "Kayıt bulunamadı (silinmiş olabilir)" : e.message);
    void qc.invalidateQueries({ queryKey: ["reminders"] });
  };
}

/** "Kart harcaması ekstrede bulunamadı": kaydı aç, ekstredeki bir satırla eşleştir (aday listesi), yoksay. */
function CardUnmatched({ r }: { r: Reminder }) {
  const ui = useUI();
  const openRow = useOpenRow();
  const fail = useAnswerError();
  const [cands, setCands] = useState(false);
  const q = useQuery({ queryKey: ["cands", r.entry_id], queryFn: () => api.linkCandidates(r.entry_id!), enabled: cands && !!r.entry_id });
  const link = useSave((txn: number) => api.linkEntry(r.entry_id!, txn), () => ui.toast("Eşleşti · iki kez sayılmaz"));
  const dismiss = useSave(() => post(r.id, "dismiss"), () => ui.toast("Tamam, bu kayıt için bir daha sormayız"));
  const open = () => void openRow(`m${r.entry_id}`, r.due, (row) => ui.openEntry(row)).then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı (silinmiş olabilir)"); });
  return (
    <li className="px-2 py-3 sm:px-3">
      <div className="flex items-start gap-3">
        <Badge r={r} />
        <div className="min-w-0 flex-1">
          <Head r={r} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button className="btn-primary px-3" aria-expanded={cands} onClick={() => setCands(!cands)}><Link2 size={14} />Ekstredeki satırla eşleştir</button>
            <button className="btn-ghost border border-stone-200 px-3 dark:border-stone-700" onClick={open}><Pencil size={14} />Kaydı aç</button>
            <button className="btn-ghost px-3" disabled={dismiss.isPending} onClick={() => dismiss.mutate(undefined, { onError: fail })}><X size={14} />Yoksay</button>
          </div>
          {cands && (
            <div className="mt-2 rounded-lg border border-stone-200 dark:border-stone-700">
              {q.isLoading && <p className="p-2 text-xs text-stone-500">Ekstrede adaylar aranıyor…</p>}
              {q.error && <p className="p-2 text-xs text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
              {q.data && !q.data.length && (
                <p className="p-2 text-xs text-stone-500 dark:text-stone-400">Ekstrede tutarı ya da tarihi yakın satır yok. Kaydı açıp ödeme şeklini düzelt
                  (kartla ödenmediyse) ya da ekstresini yükle.</p>
              )}
              {!!q.data?.length && (
                <ul className="divide-y divide-stone-100 text-xs dark:divide-stone-800">
                  {q.data.map((c) => (
                    <li key={c.id} className="flex items-center gap-2 p-2">
                      <span className="min-w-0 flex-1">
                        <span className="block break-words">{c.description}</span>
                        <span className="block text-stone-500 dark:text-stone-400">{dateTR(c.txn_date)} · {c.account}
                          {c.diff ? ` · fark ${tl(Math.abs(c.diff))}` : ""}{c.days ? ` · ${c.days} gün` : ""}</span>
                      </span>
                      <span className="shrink-0 tabular-nums">{tl(c.amount)}</span>
                      <button className="btn-primary px-2 py-1 text-xs" disabled={link.isPending} onClick={() => link.mutate(c.id, { onError: fail })}>Bu</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <Menu r={r} onDone={ui.toast} />
      </div>
    </li>
  );
}

/** "Bu havale borçtan mı?": Evet → POST /api/debts/{debt_id}/pay {amount, paid_at, entry_id}; Hayır → dismiss. */
function DebtQuestion({ r }: { r: Reminder }) {
  const ui = useUI();
  const fail = useAnswerError();
  const yes = useSave(() => call("POST", `/api/debts/${r.debt_id}/pay`, r.pay), () => ui.toast("Borçtan düşüldü · gelir/gider sayılmaz"));
  const no = useSave(() => post(r.id, "dismiss"), () => ui.toast("Tamam, ayrı hareket olarak kalır"));
  const busy = yes.isPending || no.isPending;
  return (
    <li className="px-2 py-3 sm:px-3">
      <div className="flex items-start gap-3">
        <Badge r={r} />
        <div className="min-w-0 flex-1">
          <Head r={r} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button className="btn-primary px-3" disabled={busy || !r.pay || !r.debt_id} onClick={() => yes.mutate(undefined, { onError: fail })}>
              <Check size={14} />{r.cta || "Evet, borçtan"}</button>
            <button className="btn-ghost border border-stone-200 px-3 dark:border-stone-700" disabled={busy} onClick={() => no.mutate(undefined, { onError: fail })}>
              <X size={14} />Hayır</button>
            <button className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={() => ui.go("borclar")}>Borç / Alacak'a bak</button>
          </div>
        </div>
        <Menu r={r} onDone={ui.toast} />
      </div>
    </li>
  );
}

/** Özet'te "Yapılacaklar": kesilen ekstre, ayı kapanan hesap dökümü, yaklaşan son ödeme. Boşsa hiç görünmez. */
export default function Reminders() {
  const ui = useUI();
  const q = useQuery({
    queryKey: ["reminders"], staleTime: 60_000,
    queryFn: () => fetch("/api/reminders").then(async (r) => ((r.ok && await bodyOf(r)) || []) as Reminder[]),
  });
  const pay = useQuery({ queryKey: ["payables"], queryFn: api.payables });
  const [all, setAll] = useState(false);
  const answers = useAnswers();
  const items = q.data ?? [];
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 3);
  const run = (r: Reminder) => {
    const p = r.action === "pay" ? pay.data?.find((x) => x.type === "statement" && x.id === r.statement_id) : undefined;
    if (p) return ui.openPay(p);
    ui.go(r.action === "upload" ? "ekstreler" : r.route);
  };
  return (
    <section className="card overflow-visible">
      <div className="flex items-baseline justify-between gap-2 px-3 pt-3 sm:px-4">
        <h2 className="font-display text-lg font-semibold tracking-tight">Yapılacaklar
          <span className="ml-1.5 align-middle text-xs font-medium text-stone-400 tabular-nums">{items.length}</span></h2>
        {items.length > 3 && (
          <button className="text-sm text-blue-600 hover:underline dark:text-blue-400" onClick={() => setAll(!all)}>
            {all ? "Daha az" : "Tümü"}</button>
        )}
      </div>
      <ul className="divide-y divide-stone-100 px-1 pb-1 dark:divide-stone-800">
        {shown.map((r) => r.kind === "esles" || r.kind === "hesap" ? <Question key={r.id} r={r} a={answers} />
          : r.kind === "kart_eslesmedi" ? <CardUnmatched key={r.id} r={r} />
          : r.kind === "borc_odeme" ? <DebtQuestion key={r.id} r={r} /> : (
          <li key={r.id} className="flex items-center gap-3 px-2 py-2.5 sm:px-3">
            <Badge r={r} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="break-words text-sm font-semibold">{r.title}</span>
                {r.tag && <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", TAG[r.severity])}>{r.tag}</span>}
              </div>
              <p className="break-words text-xs text-stone-500 dark:text-stone-400" title={r.text}>{r.text}</p>
            </div>
            <button className="btn-primary shrink-0 px-3" onClick={() => run(r)}>
              {r.action === "upload" ? <><FileText size={14} />Yükle</> : "Bak"}</button>
            <Menu r={r} onDone={ui.toast} />
          </li>
        ))}
      </ul>
    </section>
  );
}
