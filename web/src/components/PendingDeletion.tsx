import { useQueryClient } from "@tanstack/react-query";
import { RotateCcw, TriangleAlert } from "lucide-react";
import { useState } from "react";

/** Sunucunun 409 account_pending_deletion yanıtı (giriş, Google/Apple, şifre sıfırlama). */
export interface PendingDeletionInfo {
  detail: "account_pending_deletion";
  delete_after: string | null;
  username: string;
  restorable: boolean;
  restore_token: string | null;
  subscription?: { active: boolean; will_renew: boolean; until: string | null; store: string | null };
}

export const isPendingDeletion = (j: unknown): j is PendingDeletionInfo =>
  !!j && typeof j === "object" && (j as { detail?: unknown }).detail === "account_pending_deletion";

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const EK = ["ta", "ta", "ta", "da", "ta", "da", "da", "ta", "de", "de", "da", "ta"]; // bulunma eki: Kasım’da, Aralık’ta
/** "6 Kasım" (başka yıldaysa "6 Kasım 2027") */
export function dayMonth(iso: string | null | undefined): string {
  const d = new Date(iso ?? "");
  if (!iso || Number.isNaN(d.getTime())) return iso?.slice(0, 10) ?? "";
  return `${d.getDate()} ${AYLAR[d.getMonth()]}${d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : ""}`;
}

/** "6 Kasım’da" (başka yıldaysa "6 Kasım 2027 tarihinde"); yerel saatle */
export function onDate(iso: string | null | undefined): string {
  if (!iso) return "yakında";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const m = d.getMonth();
  return d.getFullYear() !== new Date().getFullYear()
    ? `${d.getDate()} ${AYLAR[m]} ${d.getFullYear()} tarihinde` : `${d.getDate()} ${AYLAR[m]}’${EK[m]}`;
}

/** Girişte: hesap silinmek üzere. Geri al → POST /api/me/restore (oturum çerezi gelir) → uygulama açılır. */
export default function PendingDeletion({ info, onCancel }: { info: PendingDeletionInfo; onCancel: () => void }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const when = onDate(info.delete_after);

  async function restore() {
    if (!info.restore_token || busy) return;
    setBusy(true);
    setErr(null);
    const r = await fetch("/api/me/restore", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ restore_token: info.restore_token }) });
    if (!r.ok) {
      setBusy(false);
      return setErr((await r.json().catch(() => ({}))).detail ?? "Geri alınamadı");
    }
    try { sessionStorage.setItem("hk-flash", "Hesabın geri geldi; hiçbir şey silinmedi"); } catch { /* özel mod */ }
    window.location.hash = "/ozet";
    void qc.invalidateQueries();
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="card w-full max-w-sm space-y-4 p-6" role="alertdialog" aria-labelledby="pd-title">
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex size-14 items-center justify-center rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400">
            <TriangleAlert size={26} />
          </span>
          <h1 id="pd-title" className="text-lg font-semibold">Hesabın silinmek üzere</h1>
          <p className="text-sm text-stone-600 dark:text-stone-300">
            {info.restorable
              ? <>Hesabın <b>{when}</b> kalıcı olarak silinecek. Geri almak ister misin? Verilerin olduğu gibi duruyor.</>
              : <>Bu defterin sahibi hesabını sildi; senin girişin de <b>{when}</b> silinecek. Yalnız defter sahibi geri alabilir.</>}
          </p>
          <p className="text-xs text-stone-500">{info.username}</p>
        </div>
        {info.subscription?.active && info.subscription.will_renew && (
          <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            Mağaza aboneliğin hâlâ yenileniyor.
          </p>
        )}
        {err && <p role="alert" className="text-sm text-red-600">{err}</p>}
        {info.restorable && (
          <button className="btn-primary w-full justify-center py-2" disabled={busy} onClick={() => void restore()}>
            <RotateCcw size={16} /> {busy ? "Geri alınıyor…" : "Hesabımı geri al"}
          </button>
        )}
        <button className="btn-ghost w-full justify-center border border-stone-300 py-2 dark:border-stone-700" onClick={onCancel}>
          {info.restorable ? "Hayır, silinsin" : "Tamam"}
        </button>
      </div>
    </div>
  );
}
