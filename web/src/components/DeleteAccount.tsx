import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { CircleCheck, Download, ExternalLink, RefreshCw, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { dayMonth, onDate } from "./PendingDeletion";

/** Silme onayı: sunucuyla aynı kural — büyük/küçük harf fark etmez, "sil" / "SIL" / "SİL" kabul. */
const confirmed = (s: string) => s.trim().toUpperCase().replace(/I/g, "İ") === "SİL";

interface Sub { active: boolean; will_renew: boolean; until: string | null; store: "app_store" | "play_store" | null }
const MANAGE: Record<string, [string, string]> = {
  app_store: ["App Store", "https://apps.apple.com/account/subscriptions"],
  play_store: ["Google Play", "https://play.google.com/store/account/subscriptions"],
};

type Step = "kapali" | "abonelik" | "onay" | "bitti";

/** Ayarlar → Hesabımı sil. Akış (mobil ile aynı): mağaza aboneliği aktifse önce abonelik adımı (engel değil) →
 *  veriyi indir önerisi + şifre / "SİL" onayı → "Hesabın kapatıldı, 7 gün içinde geri alabilirsin" → oturum kapanır.
 *  Hesap hemen kapanır, bekleme süresi sonunda kalıcı silinir (sunucu: app/deletion.py). Yönetici için gösterilmez. */
export default function DeleteAccount() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => send<any>("/api/auth/status") });
  // password_set: false → Google ile açılmış, şifresi hiç konmamış hesap (alan yoksa şifreli say)
  const hasPassword = (auth.data as { user?: { password_set?: boolean } | null } | undefined)?.user?.password_set !== false;
  const grace = (auth.data as { delete_grace_days?: number } | undefined)?.delete_grace_days ?? 7; // 0: hemen kalıcı
  const later = grace ? `Hesabın hemen kapanır; ${grace} gün içinde giriş yaparak geri alabilirsin. Sonra` : "Hesabın ve";
  const [step, setStep] = useState<Step>("kapali");
  const sub = useQuery({
    queryKey: ["my-subscription"], enabled: step !== "kapali", staleTime: 0,
    queryFn: () => fetch("/api/me/subscription").then((r) => (r.ok ? r.json() as Promise<Sub> : null)),
  });
  const [pw, setPw] = useState("");
  const [onay, setOnay] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [until, setUntil] = useState<string | null>(null);
  const ready = hasPassword ? !!pw : confirmed(onay);
  const s = sub.data;
  const store = MANAGE[s?.store ?? ""] ?? null;

  function start() {
    setStep("onay");
    void sub.refetch().then((r) => { if (r.data?.active) setStep("abonelik"); });
  }
  async function remove() {
    if (!ready || busy) return;
    setBusy(true);
    setErr(null);
    const body = hasPassword ? { password: pw } : { confirm: onay.trim() };
    let j: { delete_after?: string | null };
    try {
      j = await send("/api/me/delete", { method: "POST", json: body });
    } catch (x) {
      return setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined); // oturum çerezi kalmasın
    setUntil(j.delete_after ?? null);
    setStep("bitti");
  }
  const cancel = () => { setStep("kapali"); setPw(""); setOnay(""); setErr(null); };
  const leave = () => { window.location.hash = "/"; window.location.reload(); };

  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold text-red-700 dark:text-red-400"><Trash2 size={18} /> Hesabımı sil</h2>

      {step === "kapali" && (
        <>
          <p className="text-sm text-stone-500">{later} bütün verin (ekstreler, fişler, kayıtlar) kalıcı olarak silinir.</p>
          <button className="btn-danger border border-red-200 dark:border-red-900" onClick={start}>Hesabımı sil…</button>
        </>
      )}

      {step === "abonelik" && s?.active && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
            <span className="font-medium">{store?.[0] ?? "Mağaza"} aboneliği</span>
            {s.will_renew
              ? <span className="flex items-center gap-1 font-medium text-amber-700 dark:text-amber-400"><TriangleAlert size={15} /> Hâlâ yenileniyor</span>
              : <span className="flex items-center gap-1 font-medium text-green-700 dark:text-green-400"><CircleCheck size={15} /> İptal edildi{s.until && ` · ${onDate(s.until)} bitiyor`}</span>}
          </div>
          <p className="text-sm">
            {s.will_renew
              ? "Aboneliğin hâlâ yenileniyor. Hesabı silmek mağaza aboneliğini iptal etmez; iptal etmezsen ücret alınmaya devam eder."
              : "Aboneliğin yenilenmeyecek; dönem sonuna kadar sürer ve ücret alınmaz."}
          </p>
          <div className="flex flex-wrap gap-2">
            {s.will_renew && store && (
              <a className="btn-primary" href={store[1]} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> Aboneliği iptal et</a>
            )}
            {s.will_renew && (
              <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={sub.isFetching} onClick={() => void sub.refetch()}>
                <RefreshCw size={14} /> {sub.isFetching ? "Bakılıyor…" : "Durumu yenile"}
              </button>
            )}
            <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => setStep("onay")}>
              {s.will_renew ? "Zaten iptal ettim, devam et" : "Devam et"}
            </button>
            <button className="btn-ghost" onClick={cancel}>Vazgeç</button>
          </div>
        </div>
      )}

      {(step === "onay" || (step === "abonelik" && !s?.active)) && (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void remove(); }}>
          <p className="text-sm">{grace ? `Hesabın hemen kapanır ve ${grace} gün içinde giriş yaparak geri alabilirsin; sonra kalıcı olarak silinir.`
            : "Hesabın ve bütün verin hemen kalıcı olarak silinir; geri alınamaz."} Ortak defterin sahibiysen davet ettiğin kişilerin girişi de kapanır.</p>
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
              <span>Hesabını Google ya da Apple ile açtığın için şifren yok. Onaylamak için <b>SİL</b> yaz:</span>
              <input type="text" className="input" autoComplete="off" spellCheck={false} placeholder="SİL" value={onay} onChange={(e) => setOnay(e.target.value)} />
            </label>
          )}
          {err && <p role="alert" className="text-sm text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button type="submit" className="btn bg-red-600 text-white hover:bg-red-700" disabled={!ready || busy}>{busy ? "Kapatılıyor…" : "Hesabımı sil"}</button>
            <button type="button" className="btn-ghost" onClick={cancel}>Vazgeç</button>
          </div>
        </form>
      )}

      {step === "bitti" && (
        <div className="space-y-3" role="status">
          <p className="text-sm">
            {until ? <><b>Hesabın kapatıldı.</b> {grace} gün içinde giriş yaparak geri alabilirsin (son gün: {dayMonth(until)});
              sonra kalıcı olarak silinir.</> : <><b>Hesabın ve bütün verin silindi.</b></>}
          </p>
          <button className="btn-primary" onClick={leave}>Tamam</button>
        </div>
      )}
    </section>
  );
}
