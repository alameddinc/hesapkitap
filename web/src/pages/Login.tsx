import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { useState } from "react";
import { inApp, toLauncher } from "../components/AppServer";
import { isEmail, loginKind } from "../components/hooks";

export default function Login() {
  const qc = useQueryClient();
  const [user, setUser] = useState(() => { try { return localStorage.getItem("hk-username") ?? ""; } catch { return ""; } });
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  const open = !!status.data?.open_signup; // barındırılan sürüm: herkes hesap açabilir
  const email = loginKind(status.data) === "email"; // hazır sürüm: giriş e-postayla

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (email && !isEmail(user)) return setErr("Geçerli bir e-posta yaz");
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
          <p className="flex items-center gap-1 text-xs text-stone-500"><LockKeyhole size={12} /> {open ? "Ekstre ve fiş at, gerisini o yapsın" : "Kişisel finans · kendi sunucunda"}</p>
        </div>
        <label className="label" htmlFor="user">{email ? "E-posta" : "Kullanıcı adı ya da e-posta"}</label>
        <input id="user" className="input mb-3" type={email ? "email" : "text"} inputMode={email ? "email" : undefined}
          autoComplete={email ? "email username" : "username"} autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder={email ? "ornek@gmail.com" : "ör. ayse"} value={user} onChange={(e) => setUser(e.target.value)} autoFocus={!user} />
        <label className="label" htmlFor="pw">Şifre</label>
        <input id="pw" type="password" className="input" autoFocus={!!user} autoComplete="current-password"
          value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2" disabled={busy || !pw}>
          {busy ? "Giriş yapılıyor…" : "Giriş"}
        </button>
        {open ? (
          <a href="#/kayit" className="btn-ghost mt-3 w-full justify-center border border-stone-300 py-2 dark:border-stone-700">Yeni misin? Ücretsiz hesap aç</a>
        ) : (
          <a href="#/kayit" className="mt-3 block text-center text-xs text-stone-500 underline">Davet kodun var mı? Hesap aç</a>
        )}
        {inApp() && (
          <button type="button" className="mt-2 block w-full text-center text-xs text-stone-500 underline" onClick={toLauncher}>
            Demoyu dene ya da başka sunucuya bağlan
          </button>
        )}
      </form>
    </div>
  );
}
