import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { LockKeyhole } from "lucide-react";
import { useState } from "react";
import { inApp, toLauncher } from "../components/AppServer";
import { isEmail, loginKind } from "../components/hooks";
import VerifyEmail from "../components/VerifyEmail";
import PendingDeletion, { isPendingDeletion, type PendingDeletionInfo } from "../components/PendingDeletion";

export default function Login() {
  const qc = useQueryClient();
  const [user, setUser] = useState(() => { try { return localStorage.getItem("hk-username") ?? ""; } catch { return ""; } });
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingDeletionInfo | null>(null); // hesap silinmek üzere (409)
  const [verify, setVerify] = useState<{ email: string; message?: string } | null>(null); // e-posta doğrulanmamış (403)
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  const open = !!status.data?.open_signup; // barındırılan sürüm: herkes hesap açabilir
  const email = loginKind(status.data) === "email"; // hazır sürüm: giriş e-postayla
  const canReset = !!status.data?.reset; // sunucuda e-posta ayarlı: kodla şifre yenileme (#/sifre)

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (email && !isEmail(user)) return setErr("Geçerli bir e-posta yaz");
    setBusy(true);
    setErr(null);
    try {
      await send("/api/auth/login", { method: "POST", json: { username: user.trim() || null, password: pw } });
      try { if (user.trim()) localStorage.setItem("hk-username", user.trim()); } catch { /* özel mod */ }
      qc.invalidateQueries();
    } catch (x) {
      const { status, body } = x as { status?: number; body?: unknown };
      const b = body as { detail?: string; email?: string; message?: string } | null;
      if (status === 403 && b?.detail === "email_unverified") setVerify({ email: b.email || user.trim(), message: b.message });
      else if (status === 409 && isPendingDeletion(body)) setPending(body);
      else setErr((x as Error).message || "Giriş başarısız");
    } finally {
      setBusy(false); // ağ hatasında da düğme kilitli kalmaz
    }
  }

  if (verify) return (
    <VerifyEmail email={verify.email} onBack={() => { setVerify(null); setPw(""); }}
      intro={`${verify.message || "E-postana gönderdiğimiz kodla hesabını doğrula."} Yeni bir kod gönderdik.`} />
  );
  if (pending) return <PendingDeletion info={pending} onCancel={() => { setPending(null); setPw(""); }} />;

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
        <div className="flex items-baseline justify-between">
          <label className="label" htmlFor="pw">Şifre</label>
          {canReset && (
            <a href="#/sifre" className="mb-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
              onClick={() => { try { if (isEmail(user)) sessionStorage.setItem("hk-reset-email", user.trim()); } catch { /* özel mod */ } }}>
              Şifremi unuttum
            </a>
          )}
        </div>
        <input id="pw" type="password" className="input" autoFocus={!!user} autoComplete="current-password"
          value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        {status.data && !canReset && (
          <p className="mt-2 text-xs text-stone-500">
            {email ? "Şifreni mi unuttun? alameddinc@gmail.com adresine yaz." : "Şifreni unuttuysan sunucu yöneticisine sor."}
          </p>
        )}
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
