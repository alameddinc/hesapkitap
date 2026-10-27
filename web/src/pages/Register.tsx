import { useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import DataPrivacy from "../components/DataPrivacy";

const codeFromHash = () => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("kod") ?? "";

/** Davet koduyla hesap aç. Bağlantı: #/kayit?kod=XXXX-XXXX-XXXX */
export default function Register() {
  const qc = useQueryClient();
  const [code, setCode] = useState(codeFromHash);
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [agree, setAgree] = useState(false);
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  // barındırılan sürüm: herkes kod olmadan hesap açar; davet bağlantısıyla gelen (ortak defter) yine kodu kullanır
  const open = !!status.data?.open_signup;
  const askCode = !open || !!codeFromHash();
  const legal: { terms?: string; privacy?: string } = status.data?.legal ?? {};
  const needAgree = !!(legal.terms || legal.privacy);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setErr("Şifreler aynı değil");
    setBusy(true);
    setErr(null);
    const res = await fetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: askCode ? code.trim() : null, username: user.trim(), password: pw }),
    });
    setBusy(false);
    if (res.ok) {
      try { localStorage.setItem("hk-username", user.trim()); } catch { /* özel mod */ }
      window.location.hash = "/ozet";
      qc.invalidateQueries();
    } else setErr((await res.json().catch(() => ({}))).detail ?? "Kayıt başarısız");
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <img src="/icons/icon-192.png" alt="" className="size-16 rounded-2xl shadow-sm" />
          <h1 className="flex items-center gap-2 text-lg font-semibold"><UserPlus size={18} /> Hesap aç</h1>
          <p className="text-xs text-stone-500">Verilerin sana ait ayrı bir dosyada tutulur; uygulamada kimsenin onları görebileceği bir ekran yok.</p>
        </div>
        <div className="mb-4"><DataPrivacy mode="hosted" collapsible /></div>
        {askCode && <>
          <label className="label" htmlFor="code">Davet kodu</label>
          <input id="code" className="input mb-3 font-mono uppercase" autoCapitalize="characters" autoCorrect="off" spellCheck={false}
            placeholder="XXXX-XXXX-XXXX" value={code} onChange={(e) => setCode(e.target.value)} />
        </>}
        <label className="label" htmlFor="user">Kullanıcı adı</label>
        <input id="user" className="input mb-3" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder="ör. ayse" value={user} onChange={(e) => setUser(e.target.value)} autoFocus={!!code} />
        <label className="label" htmlFor="pw">Şifre (en az 10 karakter)</label>
        <input id="pw" type="password" className="input mb-3" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        <label className="label" htmlFor="pw2">Şifre (tekrar)</label>
        <input id="pw2" type="password" className="input" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
        {needAgree && (
          <label className="mt-3 flex items-start gap-2 text-xs text-stone-600 dark:text-stone-400">
            <input type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>
              {legal.terms && <a className="underline" href={legal.terms} target="_blank" rel="noreferrer">Kullanım koşullarını</a>}
              {legal.terms && legal.privacy && " ve "}
              {legal.privacy && <a className="underline" href={legal.privacy} target="_blank" rel="noreferrer">Aydınlatma metnini</a>} okudum, kabul ediyorum.
            </span>
          </label>
        )}
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2"
          disabled={busy || (askCode && !code.trim()) || user.trim().length < 3 || pw.length < 10 || (needAgree && !agree)}>
          {busy ? "Açılıyor…" : "Hesabı aç"}
        </button>
        <a href="#/" className="mt-3 block text-center text-xs text-stone-500 underline">Hesabım var, giriş yap</a>
      </form>
    </div>
  );
}
