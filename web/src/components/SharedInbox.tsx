import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { send } from "../lib/apiError";
import { AlertTriangle, ChevronDown, FileText, Share2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { cx, dateTR, tl } from "../format";
import { TERIM } from "../lib/terimler";
import { useUI } from "../ui";
import { docToDraft, type DocResult } from "./AddSheet";
import { useSave } from "./hooks";

interface Item { id: number; filename: string; kind: "receipt" | "bill"; doc: DocResult; created_at: string }

/** S6: doğrulanamayan belge (e-posta / paylaşım yolu) — içe aktarılmadı, onay bekliyor. */
interface HeldDoc {
  type: "dokum" | "ekstre"; file?: string; filename?: string; verified?: boolean; verification?: string; verification_text?: string | null;
  bank?: string; iban?: string | null;
  // dokum
  rows?: { date: string; description: string; amount: number; balance?: number | null; type?: string }[]; chain_bad_count?: number;
  // ekstre
  statement?: { bank: string; card_no: string; period_end: string; due_date?: string | null; total_debt: number;
    transactions?: { txn_date: string; description: string; amount: number }[] };
  equation_diff?: number | null; reconciliation_diff?: number | null;
}
interface Held { id: number; filename: string; kind: "dokum" | "ekstre"; doc: HeldDoc; created_at: string }
type Any = Item | Held;
const isHeld = (i: Any): i is Held => i.kind === "dokum" || i.kind === "ekstre";

/** Belgeyi aç düğmesi: sunucu dosyayı veriyorsa (GET /api/inbox/{id}/file; eski sunucuda 404) görünür. */
function OpenFile({ id }: { id: number }) {
  const url = `/api/inbox/${id}/file`;
  const q = useQuery({ queryKey: ["inbox-file", id], staleTime: Infinity, retry: false,
    queryFn: async () => { const r = await fetch(url); void r.body?.cancel(); return r.ok; } });
  if (!q.data) return null;
  return <a className="btn-ghost border border-stone-300 px-2.5 py-1 text-xs dark:border-stone-700" href={url} target="_blank" rel="noreferrer">
    <FileText size={14} aria-hidden /> Belgeyi aç</a>;
}

/** Onay bekleyen döküm/ekstre: neden tutmadı, okunan satırlar, "Yine de kaydet" (onaylı) ve "At". */
function HeldCard({ i }: { i: Held }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const d = i.doc;
  const save = useSave(() => api.importInbox(i.id), (r) => ui.toast(r.message));
  const drop = useSave(() => fetch(`/api/inbox/${i.id}/silindi`, { method: "POST" }), () => ui.toast("Belge atıldı"));
  const busy = save.isPending || drop.isPending;
  const st = d.statement;
  const lines = i.kind === "dokum" ? (d.rows ?? []).map((r) => ({ date: r.date, text: r.description, amount: r.amount, balance: r.balance }))
    : (st?.transactions ?? []).map((t) => ({ date: t.txn_date, text: t.description, amount: t.amount, balance: undefined as number | null | undefined }));
  const what = i.kind === "dokum" ? "Hesap dökümü" : "Kart ekstresi";
  return (
    <li className="space-y-2 py-2.5">
      <div className="flex items-start gap-2">
        <span aria-hidden>{i.kind === "dokum" ? "🏦" : "💳"}</span>
        <div className="min-w-0 flex-1">
          <div className="font-medium break-words">{what} · {d.bank || st?.bank || "banka"}{st ? ` ${st.card_no}` : ""}</div>
          <div className="text-xs break-words text-stone-500">
            {i.filename || d.filename}{" · "}{dateTR(i.created_at.slice(0, 10))} geldi
            {st ? ` · kesim ${dateTR(st.period_end)} · ${tl(st.total_debt)}` : ` · ${lines.length} hareket`}
          </div>
        </div>
      </div>
      <p className="flex gap-1.5 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" role="note">
        <AlertTriangle size={14} className="mt-px shrink-0" aria-hidden />
        <span>{d.verification_text || `${TERIM.rakamlarTutmadi.ad}; belgeyle karşılaştır.`} Bu yüzden içe aktarılmadı.</span>
      </p>
      {lines.length > 0 && (
        <div>
          <button className="flex items-center gap-1 text-xs text-stone-500" aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronDown size={14} className={cx("transition-transform", !open && "-rotate-90")} aria-hidden /> Okunan {lines.length} satır
          </button>
          {open && (
            <ul className="mt-1 max-h-72 divide-y divide-stone-100 overflow-y-auto rounded-lg border border-stone-200 text-xs dark:divide-stone-800 dark:border-stone-700">
              {lines.map((l, k) => (
                <li key={k} className="flex gap-2 px-2 py-1">
                  <span className="shrink-0 text-stone-500 tabular-nums">{dateTR(l.date).slice(0, 5)}</span>
                  <span className="min-w-0 flex-1 break-words">{l.text}</span>
                  <span className="shrink-0 tabular-nums">{tl(l.amount)}</span>
                  {l.balance != null && <span className="w-24 shrink-0 text-right text-stone-500 tabular-nums">{tl(l.balance)}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-2.5 py-1 text-xs" disabled={busy}
          onClick={() => void ask(`${what} yine de kaydedilsin mi?\n\nRakamlar tutmadı; belgeyle karşılaştırıp doğru olduğunu gördüysen kaydet. Yanlışsa Ekstreler → Son yüklemeler'den geri alınır.`,
            { confirm: "Yine de kaydet", danger: false }).then((ok) => { if (ok) save.mutate(undefined); })}>
          {save.isPending ? "Kaydediliyor…" : "Yine de kaydet"}</button>
        <OpenFile id={i.id} />
        <div className="flex-1" />
        <button className="btn-ghost px-2 py-1 text-xs text-red-600 dark:text-red-400" disabled={busy}
          onClick={() => void ask(`${what} atılsın mı?\n\nKaydedilmez; doğrusunu Ekstreler'den yükleyebilirsin.`, { confirm: "At", danger: true, irreversible: false })
            .then((ok) => { if (ok) drop.mutate(undefined); })}><Trash2 size={14} aria-hidden /> At</button>
      </div>
    </li>
  );
}

/** Telefondan paylaşılan fiş/faturalar: okundu, onay bekliyor. Dokununca form dolu açılır. */
export default function SharedInbox() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["inbox"], queryFn: () => send<Any[]>("/api/inbox") });
  const close = useSave((a: { id: number; action: "kaydedildi" | "silindi" }) => fetch(`/api/inbox/${a.id}/${a.action}`, { method: "POST" }));
  // Android paylaşımından dönüşte sonucu göster (#/ozet?paylasim=…)
  useEffect(() => {
    const m = window.location.hash.match(/paylasim=([^&]*)/);
    if (m) {
      ui.toast(decodeURIComponent(m[1]) || "Paylaşıldı");
      window.history.replaceState(null, "", window.location.hash.replace(/\?paylasim=[^&]*/, ""));
    }
  }, [ui]);
  const shared = (q.data ?? []).filter((i): i is Item => !isHeld(i));
  const held = (q.data ?? []).filter(isHeld);
  if (!shared.length && !held.length) return null;
  const open = (i: Item) => {
    close.mutate({ id: i.id, action: "kaydedildi" });
    if (i.kind === "bill") {
      const d = i.doc;
      ui.openBill({ provider: d.provider, amount: d.amount ?? undefined, due_date: d.due_date ?? undefined, category: d.category,
        description: d.description, file_name: d.file_name, confidence: d.confidence });
    } else ui.openEntry(undefined, docToDraft(i.doc));
  };
  return (<>
    {held.length > 0 && (
      <section className="card scroll-mt-16 border-amber-300 p-3 dark:border-amber-800" id="gelen-kutusu">
        <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><AlertTriangle size={16} className="text-amber-600" aria-hidden />
          Onay bekleyen belgeler · {held.length}</h2>
        <p className="text-xs text-stone-500">E-postayla ya da telefondan gelen bu belgelerde rakamlar tutmadı; sen bakmadan kaydedilmedi.</p>
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">{held.map((i) => <HeldCard key={i.id} i={i} />)}</ul>
      </section>
    )}
    {shared.length > 0 && (
    <section className="card p-3">
      <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><Share2 size={16} /> Telefondan paylaşılanlar · onay bekliyor</h2>
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {shared.map((i) => (
          <li key={i.id} className="flex items-center justify-between gap-2 py-2">
            <button className="min-w-0 flex-1 text-left" onClick={() => open(i)}>
              {i.kind === "bill" ? "🧾 " : "🛍 "}
              <b>{i.kind === "bill" ? i.doc.provider : i.doc.merchant}</b>
              {" · "}{tl((i.kind === "bill" ? i.doc.amount : i.doc.total) ?? 0)}
              {(i.doc.installments ?? 1) > 1 && ` · ${i.doc.installments} taksit`}
              <span className="block text-xs text-stone-500">Dokun: kontrol et ve kaydet</span>
            </button>
            <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil" disabled={close.isPending}
              onClick={() => void ask("Paylaşılan belge silinsin mi?\n\nKaydedilmeden atılır.").then((ok) => { if (ok) close.mutate({ id: i.id, action: "silindi" }); })}><Trash2 size={14} /></button>
          </li>
        ))}
      </ul>
    </section>
    )}
  </>);
}
