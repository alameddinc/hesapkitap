import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import DataPrivacy from "../components/DataPrivacy";

/** İlk açılış: şifre belirle. Uzaktan erişimde container log'undaki kurulum kodu istenir. */
export default function Setup({ codeNeeded }: { codeNeeded: boolean }) {
  const qc = useQueryClient();
  const [user, setUser] = useState("admin");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setErr("Şifreler aynı değil");
    setBusy(true);
    setErr(null);
    const res = await fetch("/api/auth/setup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user.trim() || "admin", password: pw, code: code || null }),
    });
    setBusy(false);
    if (res.ok) qc.invalidateQueries();
    else setErr((await res.json().catch(() => ({}))).detail ?? "Kurulum başarısız");
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-3 p-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <img src="/icons/icon-192.png" alt="" className="size-16 rounded-2xl shadow-sm" />
          <h1 className="text-lg font-semibold">HesapKitap'a hoş geldin</h1>
          <p className="text-sm text-stone-500">Yönetici hesabını oluştur. Verilerin sadece bu sunucuda durur.</p>
        </div>
        <DataPrivacy mode="self" collapsible />
        <div>
          <label className="label" htmlFor="s-user">Kullanıcı adı</label>
          <input id="s-user" className="input" autoComplete="username" autoCapitalize="none" value={user} onChange={(e) => setUser(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="s-pw">Şifre (en az 10 karakter)</label>
          <input id="s-pw" type="password" className="input" autoComplete="new-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="s-pw2">Şifre (tekrar)</label>
          <input id="s-pw2" type="password" className="input" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
        </div>
        {codeNeeded && (
          <div>
            <label className="label" htmlFor="s-code">Kurulum kodu</label>
            <input id="s-code" className="input tracking-widest" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
            <p className="mt-1 text-xs text-stone-500">
              Uzaktan bağlandığın için güvenlik kodu gerekiyor. Sunucuda: <code>docker compose logs | grep kurulum</code>
            </p>
          </div>
        )}
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className="btn-primary w-full justify-center py-2" disabled={busy || pw.length < 10}>
          <ShieldCheck size={16} /> {busy ? "Kaydediliyor…" : "Şifreyi belirle ve başla"}
        </button>
      </form>
    </div>
  );
}
