import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send as apiSend } from "../lib/apiError";
import { AlertTriangle, CheckCircle2, FileQuestion, FileUp, KeyRound, Landmark, Loader2, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { api, type DocKind, type UploadResult } from "../api";
import BankImport from "../components/BankImport";
import RecentImports from "../components/RecentImports";
import { useDemo } from "../components/hooks";
import { useSave } from "../components/hooks";
import { cx, dateTR, tl } from "../format";
import { useUI } from "../ui";
import StatementHelp from "../components/StatementHelp";
import StatementCard from "../components/StatementCard";
import { QuotaFullModal, useDocQuota, type QuotaFull } from "../components/DocQuota";
import { prepare, uploadInBatches } from "../lib/batchUpload";

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
  const [asking, setAsking] = useState<File[]>([]); // türünden emin olunamayan dosyalar: kullanıcıya sorulur
  // hazır sürüm: belge okuma hakkı doldu → dosya seçici açılmaz, elle gider/gelir önerilir
  const quota = useDocQuota();
  const [full, setFull] = useState<Partial<QuotaFull> | null>(null);
  const pick = () => (quota.full ? setFull({}) : input.current?.click());
  // yükleme: en fazla 5'erli partiler sırayla (lib/batchUpload.ts); ilerleme "X/Y belge"
  const qc = useQueryClient();
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const busy = progress != null;
  const sendFiles = async (files: File[], password?: string, kind?: DocKind) => {
    if (busy) return;
    const retry = !!password || !!kind; // şifre / tür sorusunun cevabı: o dosyaların sonucu listede yerini alır
    setProgress({ done: 0, total: files.length });
    try {
      const { send, skipped } = await prepare(files);
      if (!retry) setResults(skipped);
      const res = [...skipped, ...await uploadInBatches(send, {
        password, remember, kind,
        onProgress: (done, total) => setProgress({ done: done + skipped.length, total: total + skipped.length }),
        onBatch: (part) => { if (!retry) setResults((old) => [...old, ...part]); },
      })];
      if (retry) setResults((old) => [...old.filter((o) => !files.some((f) => f.name === o.filename)), ...res]);
      if (password) setPw("");
      setAsking((a) => [...(retry ? a.filter((f) => !files.includes(f)) : []),
        ...files.filter((f) => res.some((r) => r.filename === f.name && r.status === "ask"))]);
      setLocked(files.filter((f) => res.some((r) => r.filename === f.name && r.status === "password_required")));
      const acc = files.filter((f) => res.some((r) => r.filename === f.name && r.status === "account_statement"));
      if (acc.length) setQueue((q) => [...q, ...acc.map((file) => ({ file, password }))]);
      const qf = res.find((r) => r.quota_full);
      if (qf) setFull({ upgrade: qf.upgrade, limit: qf.limit });
    } finally {
      setProgress(null);
      void qc.invalidateQueries();
      void quota.refresh();
    }
  };

  const demo = useDemo().data;
  const sample = useSave((key: string) => apiSend<UploadResult[]>(`/api/demo/sample/${key}`, { method: "POST" }), (res) => setResults(res));

  const send = (files: FileList | null) => {
    if (quota.full) return setFull({});
    const ok = Array.from(files ?? []).filter((f) => /\.(pdf|xlsx|xls|csv)$/i.test(f.name));
    if (ok.length) void sendFiles(ok);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ekstreler</h1>
      <StatementHelp />

      {demo ? (
        <div className="card space-y-3 border-2 border-dashed p-6 text-center">
          <FileUp className="mx-auto text-blue-600" />
          <div className="font-medium">Demoda kendi dosyan yüklenmez — örnek bir ekstreyi okut</div>
          <div className="flex flex-wrap justify-center gap-2">
            {demo.samples.filter((s) => s.kind === "statement").map((s) => (
              <button key={s.key} className="btn-primary" disabled={sample.isPending} onClick={() => sample.mutate(s.key, { onError: (e) => ui.error(e.message) })}>
                {sample.isPending && sample.variables === s.key ? <Loader2 size={16} className="animate-spin" /> : <FileUp size={16} />} {s.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-stone-500">Gerçek yükleme yolundan geçer: okunur, rakamlar ekstreyle karşılaştırılır, işlemler kategorilenir; Garanti örneği hazır
            okuyucusu olmayan bankalar için genel okuyucuyu gösterir.</p>
        </div>
      ) : (
      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
        onClick={pick} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } }}
        className={cx("card flex cursor-pointer flex-col items-center gap-2 border-2 border-dashed p-8 text-center",
          drag && "border-blue-500 bg-blue-50 dark:bg-blue-950/30")}>
        {busy ? <Loader2 className="animate-spin text-blue-600" /> : <FileUp className="text-blue-600" />}
        <div className="font-medium" aria-live="polite">{progress ? `Okunuyor: ${progress.done}/${progress.total} belge` : "Ekstre ya da hesap dökümü bırak veya seç"}</div>
        {progress && progress.total > 0 && (
          <div className="h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700" role="progressbar"
            aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.done} aria-label="Yükleme ilerlemesi">
            <div className="h-full bg-blue-600 transition-[width]" style={{ width: `${(progress.done / progress.total) * 100}%` }} />
          </div>
        )}
        <div className="text-xs text-stone-500">
          Kredi kartı ekstresi (PDF) ya da vadesiz hesap dökümü (PDF, Excel, CSV) — hangisi olduğunu kendisi anlar ·
          birden fazla dosya seçebilirsin · her banka (hazır okuyucu: VakıfBank, Kuveyt Türk; diğerleri genel okuyucu, olmazsa yapay zekâ ile)
        </div>
        <input ref={input} type="file" accept="application/pdf,.pdf,.xlsx,.xls,.csv,text/csv" multiple className="hidden" onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      </div>
      )}
      {full && <QuotaFullModal onClose={() => setFull(null)} upgrade={full.upgrade}
        manual={(["gider", "gelir"] as const).map((kind) => ({
          label: kind === "gider" ? "Elle gider" : "Elle gelir",
          onClick: () => { setFull(null); ui.openEntry(undefined, { kind }); },
        }))} />}

      {locked.length > 0 && (
        <form className="card space-y-2 border-amber-300 p-3 dark:border-amber-800"
          onSubmit={(e) => { e.preventDefault(); if (pw) void sendFiles(locked, pw); }}>
          <div className="flex items-center gap-2 text-sm font-medium"><KeyRound size={16} className="text-amber-600" />
            {locked.length} PDF şifreli
          </div>
          <p className="text-xs text-stone-500">
            Bankanın e-postasında yazan şifre (genelde TC kimlik no'nun bazı haneleri ya da doğum tarihi).
          </p>
          <div className="flex flex-wrap gap-2">
            <input className="input min-w-0 flex-1" type="password" autoComplete="off" placeholder="PDF şifresi" value={pw}
              onChange={(e) => setPw(e.target.value)} autoFocus />
            <button className="btn-primary" disabled={!pw || busy}>Aç</button>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Bu şifreyi hatırla (sonraki ekstreler sormadan açılır; Ayarlar'dan silinebilir)
          </label>
        </form>
      )}

      {results.length > 1 && (() => {
        const n = (f: (r: UploadResult) => boolean) => results.filter(f).length;
        const parts = [
          [n((r) => r.status === "saved" || r.status === "account_statement"), "okundu"],
          [n((r) => r.status === "duplicate"), "zaten vardı / aynı dosya"],
          [n((r) => r.status === "ask" || r.status === "password_required"), "senden bilgi bekliyor"],
          [n((r) => r.status === "error"), "okunamadı"],
        ].filter(([c]) => c) as [number, string][];
        return (
          <p className="text-sm text-stone-600 dark:text-stone-300" role="status">
            {results.length} dosya: {parts.map(([c, l]) => `${c} ${l}`).join(" · ")}
            {progress ? " (sürüyor)" : ""}
          </p>
        );
      })()}
      {results.length > 0 && (
        <ul className="space-y-2">
          {results.map((r, i) => (
            <li key={i} className="card flex items-start gap-3 p-3 text-sm">
              {r.status === "account_statement" ? <Landmark className="text-blue-600" size={18} />
                : r.status === "ask" ? <FileQuestion className="text-amber-600" size={18} />
                : r.status === "error" ? <XCircle className="text-red-600" size={18} />
                : r.status === "password_required" ? <KeyRound className="text-amber-600" size={18} />
                : <CheckCircle2 className="text-green-600" size={18} />}
              <div>
                <div className="font-medium">{r.filename}</div>
                {r.status === "error" && <div className="text-red-600">{r.message}</div>}
                {r.status === "password_required" && <div className="text-amber-600">{r.message} — yukarıdan şifreyi gir</div>}
                {r.status === "duplicate" && <div className="text-stone-500 dark:text-stone-400">{r.message ?? "Zaten yüklü, atlandı."}</div>}
                {r.status === "ask" && (() => {
                  const file = asking.find((f) => f.name === r.filename);
                  const opts: [DocKind, string, string][] = [
                    ["account", "Hesap dökümü", "vadesiz hesabın hareket listesi (bakiyeli)"],
                    ["card", "Kredi kartı ekstresi", "aylık kart hesap özeti"],
                    ["dekont", "Dekont", "tek bir havale / EFT / FAST belgesi"],
                  ];
                  return (
                    <div className="space-y-2">
                      <div className="text-amber-700 dark:text-amber-400">
                        Bu belgenin ne olduğundan emin olamadım; yanlış tahmin bakiyeni bozabilir. Hangisi?
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {opts.map(([k, label, hint]) => (
                          <button key={k} className={cx("btn-ghost border border-stone-200 text-left dark:border-stone-700", k === r.guess && "ring-2 ring-blue-400")} title={hint}
                            disabled={!file || busy} onClick={() => file && void sendFiles([file], undefined, k)}>
                            {label}{k === r.guess && <span className="ml-1 text-xs text-stone-500">(tahminim)</span>}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}
                {r.status === "account_statement" && <div className="text-blue-700 dark:text-blue-400">Hesap dökümü → aşağıda önizle ve içe aktar</div>}
                {r.status === "saved" && r.dekont && (
                  <div className="text-stone-500">{r.message}
                    <div className="text-xs">Dekont tek işlemdir: hesaba eklendi, bakiyeye dokunulmadı. O ayın dökümü gelince aynı işlemle birleşir.</div>
                  </div>
                )}
                {r.status === "saved" && r.card_excel && (
                  <div className="text-stone-500">
                    Kart dökümü (Excel) · {r.bank} {r.card_no}: <b>{r.card_excel.statements} aylık ekstre</b>, {r.card_excel.transactions} işlem
                    {r.card_excel.range && ` · ${dateTR(r.card_excel.range[0])} – ${dateTR(r.card_excel.range[1])}`}
                    <div className="text-xs">PDF ekstresi zaten yüklü olan dönemler atlandı; kesim günü {r.card_excel.cut_day}.</div>
                  </div>
                )}
                {r.status === "saved" && !r.card_excel && !r.dekont && (
                  <div className="text-stone-500">
                    {r.bank} {r.card_no} · kesim {dateTR(r.period_end!)} · {r.txn_count} işlem · {tl(r.total_debt!)}
                    {!!r.linked && <div className="text-blue-700 dark:text-blue-400">{r.linked} elle girilen kredi kartı gideri bu ekstreyle eşleşti</div>}
                    {r.ai && <div className="text-violet-700 dark:text-violet-400">Bu banka için hazır okuyucu yok; yapay zekâ ile okundu</div>}
                    {r.cycle_day && <div className="text-blue-700 dark:text-blue-400">Ay dönümü kesim günlerine göre her ayın {r.cycle_day}. gününe ayarlandı</div>}
                    {r.verification === "dogrulanamadi" ? (
                      // S6: denklem/işlem toplamı tutmadı — kayıtlı ama kontrol bekliyor (aşağıdaki kartta "Doğru, onayla")
                      <div className="mt-1 flex gap-1.5 rounded-lg border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status">
                        <AlertTriangle size={14} className="mt-px shrink-0" aria-hidden />
                        <span>{r.verification_text || "Rakamlar tutmadı."} Kaydedildi; doğruysa aşağıdaki ekstrede "Doğru, onayla" de.</span>
                      </div>
                    ) : (
                    <div className={r.reconciliation_diff === 0 ? "text-green-700 dark:text-green-400" : "text-amber-600"}>
                      {r.reconciliation_diff === 0 ? "Rakamlar tuttu: tüm işlemler okundu" : `Rakamlar tutmadı: ${tl(Math.abs(r.reconciliation_diff!))} fark`}
                    </div>)}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <BankImport incoming={queue[0] ?? null} onDone={() => setQueue((q) => q.slice(1))} />

      <RecentImports />

      <ul className="space-y-2">
        {list.data?.map((s) => <StatementCard key={s.id} s={s} />)}
      </ul>
    </div>
  );
}
