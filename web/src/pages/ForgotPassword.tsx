import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { isEmail } from "../components/hooks";
import PendingDeletion, { isPendingDeletion, type PendingDeletionInfo } from "../components/PendingDeletion";

const WAIT = 60; // sn: sunucu aynı hesaba dakikada bir kod gönderir
/** Girişten gelirken yazılmış e-posta (Login bırakır) ya da son girilen hesap. */
const initialEmail = () => {
  try {
    const v = sessionStorage.getItem("hk-reset-email") ?? localStorage.getItem("hk-username") ?? "";
    return isEmail(v) ? v : "";
  } catch { return ""; }
};

/** Şifre gücü 0–4; 10 karakterin altı 0 (sunucu kabul etmez). Mobil istemcideki hesapla aynı. */
function passwordScore(pw: string): number {
  if (pw.length < 10) return 0;
  let s = 1;
  if (/[a-zçğıöşü]/.test(pw) && /[A-ZÇĞİÖŞÜ]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^\p{L}\d]/u.test(pw) || pw.length >= 16) s++;
  if (/^(.)\1+$/.test(pw) || /^(0123456789|1234567890|qwertyuiop)/i.test(pw)) s = 1;
  return Math.min(4, s);
}
const STRENGTH = [
  ["Çok kısa", "bg-red-500", "text-red-600"], ["Zayıf", "bg-amber-500", "text-amber-600"], ["Fena değil", "bg-amber-500", "text-amber-600"],
  ["İyi", "bg-green-600", "text-green-600"], ["Güçlü", "bg-green-600", "text-green-600"],
] as const;

// ağ hatası ve hata yanıtı Türkçe metinli; status ve body ekli (lib/apiError send)
const post = (path: string, body: unknown) => send(path, { method: "POST", json: body });

/** #/sifre — Şifremi unuttum: 1) e-postaya 6 haneli kod  2) kod + yeni şifre → oturum açılır, ana sayfa. */
export default function ForgotPassword() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  const closed = status.data && !status.data.reset; // sunucuda e-posta ayarlı değil
  const hosted = status.data?.login === "email";
  const [email, setEmail] = useState(initialEmail);
  const [sent, setSent] = useState(""); // kod gönderilen adres: doluysa 2. adım
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ field: "email" | "code" | "pw" | "form"; msg: string } | null>(null);
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [codeFocus, setCodeFocus] = useState(false);
  const [pending, setPending] = useState<PendingDeletionInfo | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (until <= Date.now()) return;
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      if (n >= until) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [until]);
  const left = Math.max(0, Math.ceil((until - now) / 1000));
  useEffect(() => { if (sent) codeRef.current?.focus(); }, [sent]);

  async function send(again = false) {
    const e = email.trim();
    if (!isEmail(e)) return setErr({ field: "email", msg: "Geçerli bir e-posta yaz" });
    setBusy(true);
    setErr(null);
    try {
      await post("/api/auth/forgot", { email: e });
      const n = Date.now();
      setNow(n);
      setUntil(n + WAIT * 1000);
      setSent(e);
      setCode("");
      if (again) codeRef.current?.focus();
    } catch (x) {
      setErr({ field: "form", msg: (x as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (code.length < 6) return setErr({ field: "code", msg: "6 haneli kodu yaz" });
    if (pw.length < 10) return setErr({ field: "pw", msg: "Şifre en az 10 karakter olmalı" });
    setBusy(true);
    setErr(null);
    try {
      await post("/api/auth/reset", { email: sent, code, password: pw });
      try {
        localStorage.setItem("hk-username", sent);
        sessionStorage.removeItem("hk-reset-email");
        sessionStorage.setItem("hk-flash", "Şifren yenilendi"); // ana sayfa açılınca kısa bildirim
      } catch { /* özel mod */ }
      window.location.hash = "/ozet";
      qc.invalidateQueries();
    } catch (x) {
      const st = (x as { status?: number }).status;
      const body = (x as { body?: unknown }).body;
      if (st === 409 && isPendingDeletion(body)) { setPending(body); setBusy(false); return; } // yeni şifre geçerli; geri almayı sor
      if (st === 400) { setCode(""); codeRef.current?.focus(); }
      setErr({ field: st === 400 ? "code" : st === 422 ? "pw" : "form", msg: (x as Error).message });
      setBusy(false);
    }
  }

  function changeEmail() {
    setSent("");
    setCode("");
    setPw("");
    setErr(null);
  }

  const score = passwordScore(pw);
  const [sLabel, sBar, sText] = STRENGTH[score]!;
  const fieldErr = (f: "email" | "code" | "pw") => (err?.field === f ? <p role="alert" className="mt-1 text-sm text-red-600">{err.msg}</p> : null);

  if (pending) return <PendingDeletion info={pending} onCancel={() => { window.location.hash = "/"; }} />;

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form noValidate onSubmit={(e) => { e.preventDefault(); void (sent ? reset() : send()); }} className="card w-full max-w-sm p-6">
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <img src="/icons/icon-192.png" alt="" className="size-16 rounded-2xl shadow-sm" />
          <h1 className="flex items-center gap-2 text-lg font-semibold"><KeyRound size={18} /> {sent ? "Kodu gir" : "Şifremi unuttum"}</h1>
          <p className="text-xs text-stone-500">
            {sent ? <><b className="font-medium text-stone-700 dark:text-stone-300">{sent}</b> adresine 6 haneli kod gönderdik. Gelmediyse istenmeyen klasörüne bak.</>
              : "Hesabının e-postasını yaz, sana 6 haneli bir kod gönderelim."}
          </p>
        </div>

        {closed ? (
          <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            Bu sunucuda e-postayla şifre yenileme kapalı. {hosted ? "Yardım için yaz: alameddinc@gmail.com" : "Şifreni sunucu yöneticisine sor."}
          </p>
        ) : !sent ? (
          <>
            <label className="label" htmlFor="fp-email">E-posta</label>
            <input id="fp-email" className="input" type="email" inputMode="email" autoComplete="email" autoCapitalize="none" autoCorrect="off" spellCheck={false}
              placeholder="ornek@gmail.com" autoFocus value={email} aria-invalid={err?.field === "email"}
              onChange={(e) => { setEmail(e.target.value); setErr(null); }} />
            {fieldErr("email")}
          </>
        ) : (
          <>
            <label className="label" htmlFor="fp-code">6 haneli kod</label>
            {/* tek gizli input + 6 görsel kutu: yapıştırma ve tarayıcının kod önerisi (one-time-code) çalışır */}
            <div className="relative">
              <div aria-hidden="true" className="grid grid-cols-6 gap-2">
                {Array.from({ length: 6 }, (_, i) => {
                  const active = codeFocus && (i === code.length || (i === 5 && code.length === 6));
                  return (
                    <span key={i} className={`flex h-12 items-center justify-center rounded-lg border bg-white font-display text-xl font-semibold dark:bg-stone-900 ${
                      err?.field === "code" ? "border-red-500" : active ? "border-blue-500 ring-2 ring-blue-500/20" : "border-stone-300 dark:border-stone-700"}`}>
                      {code[i] ?? (active ? <span className="h-5 w-0.5 animate-pulse bg-blue-500" /> : "")}
                    </span>
                  );
                })}
              </div>
              <input id="fp-code" ref={codeRef} type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6}
                value={code} aria-invalid={err?.field === "code"} onFocus={() => setCodeFocus(true)} onBlur={() => setCodeFocus(false)}
                onChange={(e) => {
                  const v = e.target.value.replace(/\D/g, "").slice(0, 6);
                  setCode(v);
                  if (err?.field === "code") setErr(null);
                  if (v.length === 6 && code.length < 6) pwRef.current?.focus();
                }}
                className="absolute inset-0 h-full w-full text-base opacity-0" style={{ caretColor: "transparent" }} />
            </div>
            {fieldErr("code")}
            <div className="mt-1 flex items-center justify-between text-xs">
              <button type="button" className="py-2 text-blue-600 hover:underline dark:text-blue-400" onClick={changeEmail}>E-postayı değiştir</button>
              <button type="button" className="py-2 tabular-nums text-blue-600 hover:underline disabled:text-stone-400 disabled:no-underline dark:text-blue-400"
                disabled={left > 0 || busy} onClick={() => void send(true)}>
                {left > 0 ? `Tekrar gönder (${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")})` : "Tekrar gönder"}
              </button>
            </div>

            <label className="label mt-2" htmlFor="fp-pw">Yeni şifre (en az 10 karakter)</label>
            <div className="relative">
              <input id="fp-pw" ref={pwRef} type={show ? "text" : "password"} className="input pr-10" autoComplete="new-password"
                value={pw} aria-invalid={err?.field === "pw"} onChange={(e) => { setPw(e.target.value); if (err?.field === "pw") setErr(null); }} />
              <button type="button" aria-label={show ? "Şifreyi gizle" : "Şifreyi göster"} aria-pressed={show} onClick={() => setShow(!show)}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-stone-400 hover:text-stone-600 dark:hover:text-stone-200">
                {show ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {fieldErr("pw")}
            <div className="mt-2" aria-live="polite">
              <div aria-hidden="true" className="grid grid-cols-4 gap-1">
                {[1, 2, 3, 4].map((i) => (
                  <span key={i} className={`h-1 rounded-full transition-colors ${pw && (score >= i || (score === 0 && i === 1)) ? sBar : "bg-stone-200 dark:bg-stone-700"}`} />
                ))}
              </div>
              <div className="mt-1 flex justify-between text-xs">
                <span className={pw.length >= 10 ? "text-green-600" : "text-stone-500"}>En az 10 karakter{pw && pw.length < 10 ? ` (${pw.length}/10)` : ""}</span>
                {pw && <span className={`font-medium ${sText}`}>{sLabel}</span>}
              </div>
            </div>
          </>
        )}

        {err?.field === "form" && <p role="alert" className="mt-2 text-sm text-red-600">{err.msg}</p>}
        {!closed && (
          <button className="btn-primary mt-4 w-full justify-center py-2" disabled={busy || !status.data}>
            {busy ? "Bekle…" : sent ? "Şifremi yenile" : "Kod gönder"}
          </button>
        )}
        <a href="#/" className="mt-3 block text-center text-xs text-stone-500 underline">Girişe dön</a>
      </form>
    </div>
  );
}
