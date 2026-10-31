import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Monitor, Smartphone, KeyRound } from "lucide-react";
import { cx, parseTs } from "../format";
import { ask } from "../lib/ask";
import { send } from "../lib/apiError";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Sess { id: string; kind: "web" | "mobile" | "eski"; device: string; created_at: string; last_seen: string; current: boolean }

// DİKKAT: bu uçta zamanlar sunucunun yerel saatiyle ve saat dilimi yazılmadan geliyor (app/sessions.py time.localtime;
// kurulumlarda TZ=Europe/Istanbul) — diğer *_at alanları gibi UTC değil. Saat dilimi yazılıysa parseTs, yoksa yerel.
const when = (ts: string) => {
  const d = /[zZ]|[+-]\d{2}:?\d{2}$/.test(ts) ? parseTs(ts) : new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 2) return "şimdi";
  if (mins < 60) return `${mins} dk önce`;
  return d.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
};
const KIND = { web: "Tarayıcı", mobile: "Telefon uygulaması", eski: "Eski oturum" } as const;

/** Ayarlar → Açık oturumlar (sunucu 214e4d8): bu cihaz işaretli; tekini kapat, diğerlerinden çık, her yerden çık. */
export default function Sessions() {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => send<Sess[]>("/api/me/sessions") });
  const one = useSave((id: string) => send(`/api/me/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }), () => ui.toast("Oturum kapatıldı"));
  const others = useSave(() => send<{ revoked: number }>("/api/me/sessions/revoke-all", { method: "POST", json: { keep_current: true } }),
    () => ui.toast("Diğer tüm cihazlardan çıkıldı; bu cihazda oturumun açık"));
  const all = useSave(() => send("/api/me/sessions/revoke-all", { method: "POST", json: { keep_current: false } }), () => {
    try { sessionStorage.setItem("hk-session-ended", "Her yerden çıkış yaptın. Yeniden giriş yap."); } catch { /* özel mod */ }
    void qc.invalidateQueries({ queryKey: ["auth"] });
  });
  const list = q.data ?? [];
  const otherCount = list.filter((s) => !s.current).length;
  const err = (one.error || others.error || all.error || q.error) as Error | null;
  const Icon = (s: Sess) => (s.kind === "mobile" ? Smartphone : s.kind === "eski" ? KeyRound : Monitor);

  return (
    <section className="card space-y-3 p-4">
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Monitor size={18} aria-hidden /> Açık oturumlar</h2>
        <p className="text-xs text-stone-500 dark:text-stone-400">Hesabına giriş yapılmış cihazlar. Tanımadığın bir cihaz görürsen kapat ve şifreni değiştir.</p>
      </div>
      {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {!!list.length && (
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
          {list.map((s) => {
            const I = Icon(s);
            return (
              <li key={s.id} className={cx("flex items-start gap-3 p-3", s.current && "bg-blue-50/60 dark:bg-blue-950/20")}>
                <I size={18} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
                <div className="min-w-0 flex-1 text-sm">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-medium break-words">{s.device || KIND[s.kind]}</span>
                    {s.current && <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[11px] font-semibold text-blue-800 dark:bg-blue-950 dark:text-blue-300">Bu cihaz</span>}
                  </div>
                  <div className="text-xs text-stone-500 dark:text-stone-400">
                    {s.device ? `${KIND[s.kind]} · ` : ""}son kullanım {when(s.last_seen)} · giriş {when(s.created_at)}</div>
                </div>
                {!s.current && (
                  <button className="btn-danger shrink-0 px-2 py-1 text-xs" disabled={one.isPending}
                    onClick={() => void ask(`Bu oturum kapatılsın mı?\n\n${s.device || KIND[s.kind]} cihazında hesaptan çıkılır; orada yeniden giriş gerekir.`,
                      { confirm: "Oturumu kapat", danger: true, irreversible: false }).then((ok) => { if (ok) one.mutate(s.id); })}>
                    Kapat</button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="btn border border-stone-300 text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800"
          disabled={others.isPending || otherCount === 0}
          onClick={() => void ask(`Diğer tüm cihazlardan çıkılsın mı?\n\n${otherCount} cihazda oturum kapanır; bu cihazda açık kalır.`,
            { confirm: "Diğerlerinden çık", danger: true, irreversible: false }).then((ok) => { if (ok) others.mutate(undefined); })}>
          <LogOut size={15} aria-hidden /> Diğer tüm cihazlardan çık</button>
        <button className="btn-danger"
          disabled={all.isPending}
          onClick={() => void ask("Her yerden çıkılsın mı?\n\nBu cihaz dahil bütün cihazlarda oturum kapanır; her birinde yeniden giriş gerekir.",
            { confirm: "Her yerden çık", danger: true, irreversible: false }).then((ok) => { if (ok) all.mutate(undefined); })}>
          <LogOut size={15} aria-hidden /> Her yerden çık</button>
      </div>
      {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err.message}</p>}
    </section>
  );
}
