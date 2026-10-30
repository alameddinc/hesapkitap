import { ArrowRight, CheckCircle2, GitMerge, Loader2, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import { api, errStatus, movedCount, type AccountMergeResult } from "../api";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";

export interface MergeAccount { id: number; name: string; kind: string; balance: number | null; as_of: string | null }

/** "Başka hesapla birleştir": A'nın işlemleri, sabit kalemleri ve bakiye geçmişi B'ye taşınır, A silinir; geri alınabilir. */
export default function AccountMerge({ from, accounts, onClose }: { from: MergeAccount; accounts: MergeAccount[]; onClose: () => void }) {
  const ui = useUI();
  const selId = useId();
  const others = accounts.filter((a) => a.id !== from.id);
  const [into, setInto] = useState<number | null>(() => others.find((a) => a.kind === from.kind)?.id ?? others[0]?.id ?? null);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<(AccountMergeResult & { target: string }) | null>(null);
  const [undone, setUndone] = useState(false);
  const target = others.find((a) => a.id === into);

  const merge = useSave(() => api.mergeAccount(from.id, into!), (r) => setDone({ ...r, target: target?.name ?? "" }));
  const undo = useSave(({ id, force }: { id: string; force?: boolean }) => api.undoImport(id, force));
  const fail = (e: Error) => setErr(errStatus(e) === 403 ? "Hesap birleştirmeyi defterin sahibi ya da tam yetkili üye yapabilir." : e.message);

  const runUndo = () => {
    if (!done) return;
    setErr(null);
    const ok = () => { setUndone(true); ui.toast("Birleştirme geri alındı"); };
    undo.mutate({ id: done.undo_id }, {
      onError: fail,
      onSuccess: (r) => {
        if (r.ok) return ok();
        if (confirm(`Birleştirmeden sonra ${r.conflicts?.length ?? 0} satırı değiştirmişsin (ör. kategori). Geri alırsan o değişiklikler de gider. Yine de geri alınsın mı?`))
          undo.mutate({ id: done.undo_id, force: true }, { onError: fail, onSuccess: ok });
      },
    });
  };

  if (done) return (
    <Modal title="Hesaplar birleşti" onClose={onClose} footer={<>
      <div className="flex-1" />
      {!undone && (
        <button className="btn-ghost border border-stone-200 dark:border-stone-700" disabled={undo.isPending} onClick={runUndo}>
          {undo.isPending ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />} Geri al
        </button>
      )}
      <button className="btn-primary" onClick={onClose}>Tamam</button>
    </>}>
      <div className="flex items-start gap-3" role="status">
        <CheckCircle2 size={22} className={cx("mt-0.5 shrink-0", undone ? "text-stone-400" : "text-green-600 dark:text-green-400")} aria-hidden="true" />
        <div className="space-y-1 text-sm">
          {undone ? (
            <p className="font-medium">Geri alındı: “{from.name}” ve “{done.target}” yeniden ayrı hesaplar.</p>
          ) : <>
            <p className="font-medium">“{from.name}” artık “{done.target}” içinde.</p>
            <p className="text-stone-500 dark:text-stone-400">
              {movedCount(done.moved)} kayıt taşındı{done.matched ? ` · ${done.matched} kayıt elle girdiğinle eşleşti` : ""}
              {` · bakiye “${done.balance_from === "merged" ? from.name : done.target}” hesabındaki daha yeni bilgiden`}.
            </p>
          </>}
          {err && <p role="alert" className="text-red-600 dark:text-red-400">{err}</p>}
        </div>
      </div>
    </Modal>
  );

  return (
    <Modal title="Başka hesapla birleştir" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={!into || merge.isPending}
        onClick={() => { setErr(null); merge.mutate(undefined, { onError: fail }); }}>
        {merge.isPending ? <Loader2 size={14} className="animate-spin" /> : <GitMerge size={14} />} Birleştir
      </button>
    </>}>
      {!others.length ? (
        <p className="text-sm text-stone-500">Birleştirilecek başka hesap yok.</p>
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-stone-600 dark:text-stone-300">
            Aynı hesabı iki kez açtıysan (ör. biri elle, biri dökümden) tek hesapta topla.
          </p>
          <div>
            <label className="label" htmlFor={selId}>Hangi hesapla birleşsin?</label>
            <select id={selId} className="input" value={into ?? ""} onChange={(e) => setInto(Number(e.target.value))}>
              {others.map((a) => <option key={a.id} value={a.id}>{a.name}{a.balance != null ? ` · ${tl(a.balance)}` : ""}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2" aria-hidden="true">
            <Side a={from} label="Silinecek" muted />
            <ArrowRight size={16} className="shrink-0 text-stone-400" />
            {target && <Side a={target} label="Kalacak" />}
          </div>
          {target && (
            <p className="rounded-lg bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              “{from.name}” hesabının işlemleri, sabit kalemleri ve bakiye geçmişi “{target.name}” hesabına taşınır; “{from.name}” hesabı silinir. Geri alabilirsin.
            </p>
          )}
          {err && <p role="alert" className="text-red-600 dark:text-red-400">{err}</p>}
        </div>
      )}
    </Modal>
  );
}

function Side({ a, label, muted }: { a: MergeAccount; label: string; muted?: boolean }) {
  return (
    <div className={cx("min-w-0 flex-1 rounded-lg border px-3 py-2", muted
      ? "border-dashed border-stone-300 text-stone-500 dark:border-stone-700 dark:text-stone-400"
      : "border-stone-200 bg-stone-50 dark:border-stone-700 dark:bg-stone-800/60")}>
      <div className="text-[11px] font-medium tracking-wide uppercase opacity-70">{label}</div>
      <div className="truncate font-medium">{a.name}</div>
      {a.balance != null && <div className="text-xs tabular-nums opacity-80">{tl(a.balance)}{a.as_of ? ` · ${dateTR(a.as_of)}` : ""}</div>}
    </div>
  );
}
