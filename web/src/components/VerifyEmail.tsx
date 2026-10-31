import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MailCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { send } from "../lib/apiError";

const WAIT = 60; // sunucu aynı hesaba dakikada en fazla bir kod gönderir

/**
 * E-posta doğrulama adımı (sunucu 479011f): 6 haneli kod → POST /api/auth/verify-email {email, code} → oturum açılır.
 * "Kodu yeniden gönder": POST /api/auth/verify-email/resend {email} (yanıt nötr), 60 sn geri sayım.
 * Kayıttan sonra (intro: nötr metin) ve doğrulanmamış hesapla girişte (403 email_unverified) kullanılır.
 */
export default function VerifyEmail({ email, intro, onBack }: { email: string; intro: string; onBack: () => void }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // geri sayım bitiş anına göre (arka planda bekleyen sekmede de doğru): az önce kod gönderildi
  const [until, setUntil] = useState(() => Date.now() + WAIT * 1000);
  const [now, setNow] = useState(() => Date.now());
  const left = Math.max(0, Math.ceil((until - now) / 1000));
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  useEffect(() => {
    if (until <= Date.now()) return;
    const t = setInterval(() => { const n = Date.now(); setNow(n); if (n >= until) clearInterval(t); }, 1000);
    return () => clearInterval(t);
  }, [until]);
  const digits = code.replace(/\D/g, "").slice(0, 6);

  async function verify(e?: React.FormEvent, given?: string) {
    e?.preventDefault();
    const c = given ?? digits;
    if (c.length !== 6 || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await send("/api/auth/verify-email", { method: "POST", json: { email, code: c } });
      window.location.hash = "/ozet";
      await qc.invalidateQueries();
    } catch (x) {
      setErr((x as Error).message);
      setCode("");
      ref.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (left > 0) return;
    setErr(null);
    setInfo(null);
    try {
      await send("/api/auth/verify-email/resend", { method: "POST", json: { email } });
      setInfo("Yeni kod gönderildi. Gelen kutunu ve gereksiz klasörünü kontrol et.");
      const n = Date.now();
      setNow(n);
      setUntil(n + WAIT * 1000);
    } catch (x) {
      setErr((x as Error).message);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={verify} className="card w-full max-w-sm p-6">
        <div className="mb-4 flex flex-col items-center gap-2 text-center">
          <span className="flex size-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400">
            <MailCheck size={26} aria-hidden /></span>
          <h1 className="text-lg font-semibold">E-postanı doğrula</h1>
          <p className="text-sm text-stone-600 dark:text-stone-300"><b className="break-all">{email}</b></p>
          <p className="text-xs text-stone-500 dark:text-stone-400">{intro}</p>
        </div>
        <label className="label" htmlFor="vcode">6 haneli kod</label>
        <input id="vcode" ref={ref} className="input text-center font-mono text-2xl tracking-[0.5em] sm:text-2xl" inputMode="numeric"
          autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} placeholder="••••••" value={digits}
          onChange={(e) => {
            const v = e.target.value.replace(/\D/g, "").slice(0, 6);
            setCode(v);
            if (v.length === 6) void verify(undefined, v); // otomatik doldurma / yapıştırma: kendiliğinden gönder
          }} />
        {err && <p className="mt-2 text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
        {info && !err && <p className="mt-2 text-sm text-green-700 dark:text-green-400" role="status">{info}</p>}
        <button className="btn-primary mt-4 w-full justify-center py-2" disabled={busy || digits.length !== 6}>
          {busy ? "Doğrulanıyor…" : "Doğrula ve devam et"}</button>
        <button type="button" className="btn-ghost mt-2 w-full justify-center" disabled={left > 0} onClick={() => void resend()}>
          {left > 0 ? `Kodu yeniden gönder (${left} sn)` : "Kodu yeniden gönder"}</button>
        <p className="mt-3 text-xs text-stone-500 dark:text-stone-400">Kod 30 dakika geçerli. Gelmediyse gereksiz (spam) klasörüne bak.</p>
        <button type="button" className="mt-3 flex w-full items-center justify-center gap-1 text-xs text-stone-500 underline" onClick={onBack}>
          <ArrowLeft size={12} aria-hidden /> Geri dön</button>
      </form>
    </div>
  );
}
