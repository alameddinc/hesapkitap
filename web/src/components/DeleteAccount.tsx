import { useQuery } from "@tanstack/react-query";
import { Download, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMeta } from "./hooks";

/** Silme onayı: sunucuyla aynı kural — büyük/küçük harf fark etmez, "sil" / "SIL" / "SİL" kabul. */
const confirmed = (s: string) => s.trim().toUpperCase().replace(/I/g, "İ") === "SİL";

/** Ayarlar → Hesabımı sil: hesap ve tüm veri kalıcı olarak silinir. Şifreli hesap şifreyle, şifresiz (Google ile açılmış)
 *  hesap "SİL" yazarak onaylar. Yönetici için gösterilmez. */
export default function DeleteAccount() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  // password_set: false → Google ile açılmış, şifresi hiç konmamış hesap (alan yoksa şifreli say)
  const hasPassword = (auth.data as { user?: { password_set?: boolean } | null } | undefined)?.user?.password_set !== false;
  const plan = useMeta().data?.plan;
  const subscribed = !!plan?.hosted && !plan.member && (plan.plan === "premium" || plan.plan === "aile");
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [onay, setOnay] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = hasPassword ? !!pw : confirmed(onay);
  async function remove() {
    if (!ready || busy) return;
    setBusy(true);
    setErr(null);
    const body = hasPassword ? { password: pw } : { confirm: onay.trim() };
    const r = await fetch("/api/me/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) {
      setBusy(false);
      return setErr((await r.json().catch(() => ({}))).detail ?? "Silinemedi");
    }
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined); // oturum çerezi kalmasın
    window.location.hash = "/";
    window.location.reload();
  }
  const cancel = () => { setOpen(false); setPw(""); setOnay(""); setErr(null); };
  return (
    <section className="card space-y-2 p-4">
      <h2 className="flex items-center gap-2 font-semibold text-red-700 dark:text-red-400"><Trash2 size={18} /> Hesabımı sil</h2>
      {!open ? (
        <>
          <p className="text-sm text-stone-500">Hesabın ve bütün verin (ekstreler, fişler, kayıtlar) kalıcı olarak silinir.</p>
          <button className="btn-danger border border-red-200 dark:border-red-900" onClick={() => setOpen(true)}>Hesabımı sil…</button>
        </>
      ) : (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void remove(); }}>
          <p className="text-sm">Bu geri alınamaz. Ortak defterin sahibiysen davet ettiğin kişilerin girişi de silinir.</p>
          {subscribed && (
            <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              Hesabı silmek mağaza aboneliğini iptal etmez. Aboneliği App Store ya da Google Play’den aldıysan ücret alınmaya
              devam etmesin diye oradan iptal et.
            </p>
          )}
          <p className="flex flex-wrap items-center gap-2 text-sm">
            Verini önce indirmek ister misin?
            <a className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700" href="/api/settings/export.zip" download>
              <Download size={14} /> Tümünü indir (.zip)
            </a>
          </p>
          {hasPassword ? (
            <label className="block space-y-1 text-sm">
              <span>Onaylamak için şifreni yaz:</span>
              <input type="password" className="input" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
            </label>
          ) : (
            <label className="block space-y-1 text-sm">
              <span>Hesabını Google ile açtığın için şifren yok. Onaylamak için <b>SİL</b> yaz:</span>
              <input type="text" className="input" autoComplete="off" spellCheck={false} placeholder="SİL" value={onay} onChange={(e) => setOnay(e.target.value)} />
            </label>
          )}
          {err && <p className="text-sm text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button type="submit" className="btn bg-red-600 text-white hover:bg-red-700" disabled={!ready || busy}>{busy ? "Siliniyor…" : "Kalıcı olarak sil"}</button>
            <button type="button" className="btn-ghost" onClick={cancel}>Vazgeç</button>
          </div>
        </form>
      )}
    </section>
  );
}
