import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import DataPrivacy from "../components/DataPrivacy";
import VerifyEmail from "../components/VerifyEmail";
import { isEmail, loginKind } from "../components/hooks";

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
  const [verify, setVerify] = useState<string | null>(null); // kod adımı: e-posta
  const [done, setDone] = useState(false); // nötr yanıt, oturum açılmadı
  const [agree, setAgree] = useState({ kosullar: false, acik_riza: false }); // KVKK: iki onay, işaretsiz başlar
  const status = useQuery({ queryKey: ["auth"], queryFn: () => send<any>("/api/auth/status") });
  // barındırılan sürüm: herkes kod olmadan hesap açar; davet bağlantısıyla gelen (ortak defter) yine kodu kullanır
  const open = !!status.data?.open_signup;
  const askCode = !open || !!codeFromHash();
  const legal: { terms?: string; privacy?: string } = status.data?.legal ?? {};
  const consent: string[] = status.data?.consent ?? []; // hazır sürüm: ["acik_riza","kosullar"]; kendi sunucusunda boş
  const needAgree = consent.length > 0;
  const agreed = agree.kosullar && agree.acik_riza;
  let rizaUrl: string | null = null; // açık rıza metni koşullarla aynı sitede
  try { rizaUrl = legal.terms ? new URL(legal.terms).origin + "/acik-riza" : null; } catch { /* adres bozuk */ }
  const doc = (href: string | null | undefined, text: string) =>
    href ? <a className="underline" href={href} target="_blank" rel="noreferrer">{text}</a> : <span>{text}</span>;
  const email = loginKind(status.data) === "email"; // hazır sürüm: her hesap e-postayla açılır

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (email && !isEmail(user)) return setErr("Geçerli bir e-posta yaz");
    if (pw !== pw2) return setErr("Şifreler aynı değil");
    if (needAgree && !agreed) return setErr("Devam etmek için iki onay kutusunu da işaretle");
    setBusy(true);
    setErr(null);
    try {
      const r = await send<{ ok: boolean; verify_required?: boolean; email?: string }>("/api/auth/register",
        { method: "POST", json: { code: askCode ? code.trim() : null, username: user.trim(), password: pw, ...(needAgree ? { consent } : {}) } });
      try { localStorage.setItem("hk-username", user.trim()); } catch { /* özel mod */ }
      // hazır sürüm: e-posta doğrulanana kadar oturum açılmaz → kod adımı
      if (r?.verify_required) { setVerify(r.email || user.trim()); return; }
      // yanıt nötr (e-posta kayıtlıysa oturum açılmaz, adrese bilgi gider): oturum açıldı mı bak
      const st = await send<{ authenticated?: boolean }>("/api/auth/status").catch(() => null);
      if (st?.authenticated) {
        window.location.hash = "/ozet";
        qc.invalidateQueries();
      } else setDone(true);
    } catch (x) {
      setErr((x as Error).message || "Kayıt başarısız");
    } finally {
      setBusy(false);
    }
  }

  if (verify) return (
    <VerifyEmail email={verify} onBack={() => { setVerify(null); setPw(""); setPw2(""); }}
      intro="Bu adrese 6 haneli bir kod gönderdik; kodu girince hesabın açılır. Bu e-postayla zaten bir hesabın varsa kod yerine bilgi e-postası gönderdik: giriş yapabilir ya da şifreni yenileyebilirsin." />
  );
  if (done) return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="card w-full max-w-sm space-y-3 p-6 text-center" role="status">
        <h1 className="text-lg font-semibold">E-postanı kontrol et</h1>
        <p className="text-sm text-stone-600 dark:text-stone-300">
          Bu e-postayla zaten bir hesabın varsa e-postana bilgi gönderdik; giriş yapabilir ya da şifreni yenileyebilirsin.</p>
        <a href="#/" className="btn-primary w-full justify-center py-2">Giriş ekranına dön</a>
      </div>
    </div>
  );

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
        <label className="label" htmlFor="user">{email ? "E-posta" : "Kullanıcı adı"}</label>
        <input id="user" className={email ? "input mb-3" : "input"} type={email ? "email" : "text"} inputMode={email ? "email" : undefined}
          autoComplete={email ? "email username" : "username"} autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder={email ? "ornek@gmail.com" : "ör. ayse"} value={user} onChange={(e) => setUser(e.target.value)} autoFocus={!!code} />
        {!email && <p className="mb-3 mt-1 text-xs text-stone-500">İstersen e-posta adresini de yazabilirsin.</p>}
        <label className="label" htmlFor="pw">Şifre (en az 10 karakter)</label>
        <input id="pw" type="password" className="input mb-3" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        <label className="label" htmlFor="pw2">Şifre (tekrar)</label>
        <input id="pw2" type="password" className="input" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
        {needAgree && (
          <div className="mt-3 flex flex-col gap-2 text-xs text-stone-600 dark:text-stone-400">
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={agree.kosullar} onChange={(e) => setAgree({ ...agree, kosullar: e.target.checked })} />
              <span>{doc(legal.terms, "Kullanım Koşulları")} ve {doc(legal.privacy, "Aydınlatma Metni")}'ni okudum.</span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={agree.acik_riza} onChange={(e) => setAgree({ ...agree, acik_riza: e.target.checked })} />
              <span>{doc(rizaUrl, "Açık Rıza Metni")}'ni okudum, verilerimin işlenmesine onay veriyorum.</span>
            </label>
          </div>
        )}
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2"
          disabled={busy || (askCode && !code.trim()) || user.trim().length < 3 || pw.length < 10 || (needAgree && !agreed)}>
          {busy ? "Açılıyor…" : "Hesabı aç"}
        </button>
        <a href="#/" className="mt-3 block text-center text-xs text-stone-500 underline">Hesabım var, giriş yap</a>
      </form>
    </div>
  );
}
