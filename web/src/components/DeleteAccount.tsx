import { Trash2 } from "lucide-react";
import { useState } from "react";

/** Ayarlar → Hesabımı sil: hesap ve tüm veri kalıcı olarak silinir (şifreyle onay). Yönetici için gösterilmez. */
export default function DeleteAccount() {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function remove() {
    setBusy(true);
    setErr(null);
    const r = await fetch("/api/me/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
    setBusy(false);
    if (!r.ok) return setErr((await r.json().catch(() => ({}))).detail ?? "Silinemedi");
    window.location.hash = "/";
    window.location.reload();
  }
  return (
    <section className="card space-y-2 p-4">
      <h2 className="flex items-center gap-2 font-semibold text-red-700 dark:text-red-400"><Trash2 size={18} /> Hesabımı sil</h2>
      {!open ? (
        <>
          <p className="text-sm text-stone-500">Hesabın ve bütün verin (ekstreler, fişler, kayıtlar) kalıcı olarak silinir. Önce yukarıdan verini indirmek isteyebilirsin.</p>
          <button className="btn-danger border border-red-200 dark:border-red-900" onClick={() => setOpen(true)}>Hesabımı sil…</button>
        </>
      ) : (
        <>
          <p className="text-sm">Bu geri alınamaz. Ortak defterin sahibiysen davet ettiğin kişilerin girişi de silinir. Onaylamak için şifreni yaz:</p>
          <input type="password" className="input" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
          {err && <p className="text-sm text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button className="btn bg-red-600 text-white hover:bg-red-700" disabled={!pw || busy} onClick={remove}>{busy ? "Siliniyor…" : "Kalıcı olarak sil"}</button>
            <button className="btn-ghost" onClick={() => { setOpen(false); setPw(""); setErr(null); }}>Vazgeç</button>
          </div>
        </>
      )}
    </section>
  );
}
