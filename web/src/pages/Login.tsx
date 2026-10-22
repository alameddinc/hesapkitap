import { useQueryClient } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { useState } from "react";

export default function Login() {
  const qc = useQueryClient();
  const [user, setUser] = useState(() => { try { return localStorage.getItem("hk-username") ?? ""; } catch { return ""; } });
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user.trim() || null, password: pw }),
    });
    setBusy(false);
    if (res.ok) {
      try { if (user.trim()) localStorage.setItem("hk-username", user.trim()); } catch { /* özel mod */ }
      qc.invalidateQueries();
    }
    else setErr((await res.json().catch(() => ({}))).detail ?? "Giriş başarısız");
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <img src="/icons/icon-192.png" alt="" className="size-16 rounded-2xl shadow-sm" />
          <h1 className="text-lg font-semibold">HesapKitap</h1>
          <p className="flex items-center gap-1 text-xs text-stone-500"><LockKeyhole size={12} /> Kişisel finans · kendi sunucunda</p>
        </div>
        <label className="label" htmlFor="user">Kullanıcı adı</label>
        <input id="user" className="input mb-3" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder="ör. ayse" value={user} onChange={(e) => setUser(e.target.value)} autoFocus={!user} />
        <label className="label" htmlFor="pw">Şifre</label>
        <input id="pw" type="password" className="input" autoFocus={!!user} autoComplete="current-password"
          value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2" disabled={busy || !pw}>
          {busy ? "Giriş yapılıyor…" : "Giriş"}
        </button>
        <a href="#/kayit" className="mt-3 block text-center text-xs text-stone-500 underline">Davet kodun var mı? Hesap aç</a>
      </form>
    </div>
  );
}
