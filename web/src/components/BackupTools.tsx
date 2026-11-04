import { FileSpreadsheet, FolderDown, Receipt, Upload } from "lucide-react";
import { call } from "../lib/apiError";
import { useRef, useState } from "react";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import { RECEIPT_PICKS, receiptsExportUrl, type ReceiptPick } from "../lib/receiptPeriod";

interface RestoreResult { entries: number; statements: number; files: number; before: string }

const send = (url: string, body?: FormData) => call<RestoreResult>("POST", url, body);

export const restoreNamed = (name: string) => send(`/api/settings/backup/${encodeURIComponent(name)}/restore`);

export const restoredMsg = (r: RestoreResult) =>
  `Geri yüklendi: ${r.entries} kayıt, ${r.statements} ekstre${r.files ? `, ${r.files} dosya` : ""}. Önceki hal de yedeklendi.`;

/** Tümünü indir (zip) · Excel · yedekten geri yükle. */
export default function BackupTools() {
  const ui = useUI();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [pass, setPass] = useState("");
  const encrypted = !!file?.name.endsWith(".enc");
  // fiş listesi (muhasebeci için): hazır dönem ya da tarih aralığı
  const today = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; })();
  const [pick, setPick] = useState<ReceiptPick>("this_month");
  const [range, setRange] = useState({ from: `${today.slice(0, 7)}-01`, to: today });
  const rangeBad = pick === "range" && (!range.from || !range.to || range.to < range.from);
  const restore = useSave(() => {
    const fd = new FormData();
    fd.append("file", file!);
    if (pass) fd.append("passphrase", pass);
    return send("/api/settings/backup/restore", fd);
  }, (r) => { setFile(null); setPass(""); ui.toast(restoredMsg(r)); }, { inline: true });

  return (
    <div className="space-y-3 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
      <div className="flex flex-wrap gap-2">
        <a className="btn-ghost border border-stone-300 dark:border-stone-700" href="/api/settings/export.zip" download>
          <FolderDown size={15} /> Tümünü indir (.zip)
        </a>
        <a className="btn-ghost border border-stone-300 dark:border-stone-700" href="/api/settings/export.xlsx" download>
          <FileSpreadsheet size={15} /> Excel olarak indir
        </a>
        <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => input.current?.click()}>
          <Upload size={15} /> Yedekten geri yükle
        </button>
        <input ref={input} type="file" className="hidden" accept=".zip,.db,.enc"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); e.target.value = ""; }} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Receipt size={15} className="text-stone-500" />
        <span>Fiş listesi (muhasebeci için)</span>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Fiş listesinin dönemi">
          {RECEIPT_PICKS.map((p) => (
            <button key={p.key} role="radio" aria-checked={pick === p.key} onClick={() => setPick(p.key)}
              className={`rounded-full border px-2.5 py-1 text-xs ${pick === p.key
                ? "border-emerald-600 bg-emerald-50 font-medium text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                : "border-stone-300 dark:border-stone-700"}`}>{p.label}</button>
          ))}
        </div>
        {pick === "range" && (
          <>
            <input className="input w-auto py-1" type="date" aria-label="Başlangıç" value={range.from} onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} />
            <input className="input w-auto py-1" type="date" aria-label="Bitiş" value={range.to} onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} />
          </>
        )}
        {rangeBad ? <span className="text-xs text-red-600">Bitiş tarihi başlangıçtan önce olamaz</span> : (
          <>
            <a className="btn-ghost border border-stone-300 dark:border-stone-700" href={receiptsExportUrl(pick, today, "xlsx", range)} download>Excel</a>
            <a className="btn-ghost border border-stone-300 dark:border-stone-700" href={receiptsExportUrl(pick, today, "csv", range)} download>CSV</a>
          </>
        )}
      </div>
      <p className="text-xs text-stone-500">
        <b>Fiş listesi</b>: seçilen dönemin fişleri; tarih, işyeri, unvan, vergi no, vergi dairesi, toplam, KDV %1 / %10 / %20;
        Excel'de ay ay KDV özeti de var.{" "}
        <b>Zip</b>: kayıtlar + ekstre PDF'leri + faturalar; başka sunucuya taşımak ya da geri dönmek için. <b>Excel</b>: tüm
        işlemler, sabit kalemler, hesaplar; kendi tablon için (geri yüklenmez).
      </p>
      {file && (
        <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
          <p><b>{file.name}</b> geri yüklensin mi? Şu anki tüm kayıtların yerini alır. Şu anki hal önce yedeklenir; istersen
            listeden ona geri dönebilirsin.</p>
          {encrypted && (
            <input className="input" type="password" placeholder="Yedek şifresi" value={pass} onChange={(e) => setPass(e.target.value)} />
          )}
          <div className="flex gap-2">
            <button className="btn-primary" disabled={restore.isPending || (encrypted && !pass)} onClick={() => restore.mutate(undefined)}>
              {restore.isPending ? "Yükleniyor…" : "Geri yükle"}
            </button>
            <button className="btn-ghost" onClick={() => setFile(null)}>Vazgeç</button>
          </div>
          {restore.error && <p className="text-red-600">{(restore.error as Error).message}</p>}
        </div>
      )}
    </div>
  );
}
