import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Loader2, Trash2, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { api, type UploadResult } from "../api";
import { useSave } from "../components/hooks";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";

export default function Statements() {
  const ui = useUI();
  const list = useQuery({ queryKey: ["statements"], queryFn: api.statements });
  const [results, setResults] = useState<UploadResult[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = useSave((files: File[]) => api.upload(files), setResults);
  const del = useSave((id: number) => api.deleteStatement(id), () => ui.toast("Ekstre silindi"));

  const send = (files: FileList | null) => {
    const pdfs = Array.from(files ?? []).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length) upload.mutate(pdfs);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ekstreler</h1>

      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
        onClick={() => input.current?.click()} role="button" tabIndex={0}
        className={cx("card flex cursor-pointer flex-col items-center gap-2 border-2 border-dashed p-8 text-center",
          drag && "border-blue-500 bg-blue-50 dark:bg-blue-950/30")}>
        {upload.isPending ? <Loader2 className="animate-spin text-blue-600" /> : <FileUp className="text-blue-600" />}
        <div className="font-medium">{upload.isPending ? "İşleniyor…" : "PDF ekstreyi bırak veya seç"}</div>
        <div className="text-xs text-stone-500">İnternet şubesinden indirilen hesap özeti · Şu an desteklenen: VakıfBank</div>
        <input ref={input} type="file" accept="application/pdf" multiple className="hidden" onChange={(e) => send(e.target.files)} />
      </div>
      {upload.error && <p className="text-sm text-red-600">{(upload.error as Error).message}</p>}

      {results.length > 0 && (
        <ul className="space-y-2">
          {results.map((r, i) => (
            <li key={i} className="card flex items-start gap-3 p-3 text-sm">
              {r.status === "error" ? <XCircle className="text-red-600" size={18} /> : <CheckCircle2 className="text-green-600" size={18} />}
              <div>
                <div className="font-medium">{r.filename}</div>
                {r.status === "error" && <div className="text-red-600">{r.message}</div>}
                {r.status === "duplicate" && <div className="text-stone-500">Zaten yüklü, atlandı.</div>}
                {r.status === "saved" && (
                  <div className="text-stone-500">
                    {r.bank} {r.card_no} · kesim {dateTR(r.period_end!)} · {r.txn_count} işlem · {tl(r.total_debt!)}
                    {!!r.linked && <div className="text-blue-700 dark:text-blue-400">{r.linked} elle girilen kredi kartı gideri bu ekstreyle eşleşti</div>}
                    <div className={r.reconciliation_diff === 0 ? "text-green-700 dark:text-green-400" : "text-amber-600"}>
                      {r.reconciliation_diff === 0 ? "Mutabakat tamam: tüm işlemler okundu" : `Mutabakat farkı ${tl(r.reconciliation_diff!)}`}
                    </div>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <ul className="space-y-2">
        {list.data?.map((s) => (
          <li key={s.id} className="card flex items-center gap-3 p-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{s.bank} · {s.card_no}</div>
              <div className="text-xs text-stone-500">
                Kesim {dateTR(s.period_end)} · son ödeme {s.due_date ? dateTR(s.due_date) : "—"} · {s.txn_count} işlem
                {s.interest_fees > 0 && <span className="text-red-600"> · faiz {tl(s.interest_fees)}</span>}
              </div>
            </div>
            <div className="text-right font-semibold tabular-nums">{tl(s.total_debt)}</div>
            <button className="btn-danger p-2" aria-label="Sil"
              onClick={() => confirm(`${s.card_no} ${dateTR(s.period_end)} ekstresi ve tüm işlemleri silinsin mi?`) && del.mutate(s.id)}>
              <Trash2 size={16} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
