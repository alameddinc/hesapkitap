import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";

/** Yöneticinin verdiği geçici şifreyle girildi: devam etmeden önce kendi şifreni belirle. */
export default function ChangePassword({ username }: { username: string }) {
  const qc = useQueryClient();
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setErr("Şifreler aynı değil");
    setBusy(true);
    setErr(null);
    const res = await fetch("/api/settings/password", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: cur, new: pw }),
    });
    setBusy(false);
    if (res.ok) qc.invalidateQueries();
    else setErr((await res.json().catch(() => ({}))).detail ?? "Değiştirilemedi");
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <h1 className="mb-1 flex items-center gap-2 text-lg font-semibold"><KeyRound size={18} /> Kendi şifreni belirle</h1>
        <p className="mb-4 text-xs text-stone-500">
          Merhaba <b>{username}</b>. Geçici şifreyle girdin; devam etmek için sadece senin bildiğin bir şifre seç.
        </p>
        <label className="label" htmlFor="cur">Geçici şifre</label>
        <input id="cur" type="password" className="input mb-3" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        <label className="label" htmlFor="pw">Yeni şifre (en az 10 karakter)</label>
        <input id="pw" type="password" className="input mb-3" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        <label className="label" htmlFor="pw2">Yeni şifre (tekrar)</label>
        <input id="pw2" type="password" className="input" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2" disabled={busy || !cur || pw.length < 10}>Kaydet ve devam et</button>
      </form>
    </div>
  );
}
