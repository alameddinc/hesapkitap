import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileUp, KeyRound, Landmark, Loader2, Trash2, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { api, type UploadResult } from "../api";
import BankImport from "../components/BankImport";
import { useSave } from "../components/hooks";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import StatementHelp from "../components/StatementHelp";

export default function Statements() {
  const ui = useUI();
  const list = useQuery({ queryKey: ["statements"], queryFn: api.statements });
  const [results, setResults] = useState<UploadResult[]>([]);
  const [drag, setDrag] = useState(false);
  const [locked, setLocked] = useState<File[]>([]); // şifre isteyen dosyalar
  const [pw, setPw] = useState("");
  const [remember, setRemember] = useState(true);
  const input = useRef<HTMLInputElement>(null);
  // tek yükleme alanı: hesap dökümleri sırayla hesap hareketleri önizlemesine gider
  const [queue, setQueue] = useState<{ file: File; password?: string }[]>([]);
  const upload = useSave(
    ({ files, password }: { files: File[]; password?: string }) => api.upload(files, password, remember),
    (res: UploadResult[]) => {
      setResults(res);
      setPw("");
    },
  );
  const sendFiles = (files: File[], password?: string) =>
    upload.mutate({ files, password }, {
      onSuccess: (res) => {
        setLocked(files.filter((f) => res.some((r) => r.filename === f.name && r.status === "password_required")));
        const acc = files.filter((f) => res.some((r) => r.filename === f.name && r.status === "account_statement"));
        if (acc.length) setQueue((q) => [...q, ...acc.map((file) => ({ file, password }))]);
      },
    });
  const del = useSave((id: number) => api.deleteStatement(id), () => ui.toast("Ekstre silindi"));

  const send = (files: FileList | null) => {
    const ok = Array.from(files ?? []).filter((f) => /\.(pdf|xlsx|xls|csv)$/i.test(f.name));
    if (ok.length) sendFiles(ok);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ekstreler</h1>
      <StatementHelp />

      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
        onClick={() => input.current?.click()} role="button" tabIndex={0}
        className={cx("card flex cursor-pointer flex-col items-center gap-2 border-2 border-dashed p-8 text-center",
          drag && "border-blue-500 bg-blue-50 dark:bg-blue-950/30")}>
        {upload.isPending ? <Loader2 className="animate-spin text-blue-600" /> : <FileUp className="text-blue-600" />}
        <div className="font-medium">{upload.isPending ? "İşleniyor…" : "Ekstre ya da hesap dökümü bırak veya seç"}</div>
        <div className="text-xs text-stone-500">
          Kredi kartı ekstresi (PDF) ya da vadesiz hesap dökümü (PDF, Excel, CSV) — hangisi olduğunu kendisi anlar ·
          birden fazla dosya seçebilirsin · her banka (hazır okuyucu: VakıfBank, Kuveyt Türk; diğerleri yapay zekâ ile)
        </div>
        <input ref={input} type="file" accept="application/pdf,.pdf,.xlsx,.xls,.csv,text/csv" multiple className="hidden" onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      </div>
      {upload.error && <p className="text-sm text-red-600">{(upload.error as Error).message}</p>}

      {locked.length > 0 && (
        <form className="card space-y-2 border-amber-300 p-3 dark:border-amber-800"
          onSubmit={(e) => { e.preventDefault(); if (pw) sendFiles(locked, pw); }}>
          <div className="flex items-center gap-2 text-sm font-medium"><KeyRound size={16} className="text-amber-600" />
            {locked.length} PDF şifreli
          </div>
          <p className="text-xs text-stone-500">
            Bankanın e-postasında yazan şifre (genelde TC kimlik no'nun bazı haneleri ya da doğum tarihi).
          </p>
          <div className="flex flex-wrap gap-2">
            <input className="input min-w-0 flex-1" type="password" autoComplete="off" placeholder="PDF şifresi" value={pw}
              onChange={(e) => setPw(e.target.value)} autoFocus />
            <button className="btn-primary" disabled={!pw || upload.isPending}>Aç</button>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Bu şifreyi hatırla (sonraki ekstreler sormadan açılır; Ayarlar'dan silinebilir)
          </label>
        </form>
      )}

      {results.length > 0 && (
        <ul className="space-y-2">
          {results.map((r, i) => (
            <li key={i} className="card flex items-start gap-3 p-3 text-sm">
              {r.status === "account_statement" ? <Landmark className="text-blue-600" size={18} />
                : r.status === "error" ? <XCircle className="text-red-600" size={18} />
                : r.status === "password_required" ? <KeyRound className="text-amber-600" size={18} />
                : <CheckCircle2 className="text-green-600" size={18} />}
              <div>
                <div className="font-medium">{r.filename}</div>
                {r.status === "error" && <div className="text-red-600">{r.message}</div>}
                {r.status === "password_required" && <div className="text-amber-600">{r.message} — yukarıdan şifreyi gir</div>}
                {r.status === "duplicate" && <div className="text-stone-500">Zaten yüklü, atlandı.</div>}
                {r.status === "account_statement" && <div className="text-blue-700 dark:text-blue-400">Hesap dökümü → aşağıda önizle ve içe aktar</div>}
                {r.status === "saved" && r.card_excel && (
                  <div className="text-stone-500">
                    Kart dökümü (Excel) · {r.bank} {r.card_no}: <b>{r.card_excel.statements} aylık ekstre</b>, {r.card_excel.transactions} işlem
                    {r.card_excel.range && ` · ${dateTR(r.card_excel.range[0])} – ${dateTR(r.card_excel.range[1])}`}
                    <div className="text-xs">PDF ekstresi zaten yüklü olan dönemler atlandı; kesim günü {r.card_excel.cut_day}.</div>
                  </div>
                )}
                {r.status === "saved" && !r.card_excel && (
                  <div className="text-stone-500">
                    {r.bank} {r.card_no} · kesim {dateTR(r.period_end!)} · {r.txn_count} işlem · {tl(r.total_debt!)}
                    {!!r.linked && <div className="text-blue-700 dark:text-blue-400">{r.linked} elle girilen kredi kartı gideri bu ekstreyle eşleşti</div>}
                    {r.ai && <div className="text-violet-700 dark:text-violet-400">Bu banka için hazır okuyucu yok; yapay zekâ ile okundu</div>}
                    {r.cycle_day && <div className="text-blue-700 dark:text-blue-400">Ay dönümü kesim günlerine göre her ayın {r.cycle_day}. gününe ayarlandı</div>}
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

      <BankImport incoming={queue[0] ?? null} onDone={() => setQueue((q) => q.slice(1))} />

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
