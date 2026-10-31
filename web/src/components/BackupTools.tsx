import { FileSpreadsheet, FolderDown, Upload } from "lucide-react";
import { errorText } from "../lib/apiError";
import { useRef, useState } from "react";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface RestoreResult { entries: number; statements: number; files: number; before: string }

const send = async (url: string, body?: FormData): Promise<RestoreResult> => {
  const r = await fetch(url, { method: "POST", body });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errorText(j, r.status));
  return j;
};

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
  const restore = useSave(() => {
    const fd = new FormData();
    fd.append("file", file!);
    if (pass) fd.append("passphrase", pass);
    return send("/api/settings/backup/restore", fd);
  }, (r) => { setFile(null); setPass(""); ui.toast(restoredMsg(r)); });

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
      <p className="text-xs text-stone-500">
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
