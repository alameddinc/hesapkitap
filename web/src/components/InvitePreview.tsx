import { useQuery } from "@tanstack/react-query";
import { Smartphone, Users2, XCircle } from "lucide-react";
import { send } from "../lib/apiError";
import { dateTR } from "../format";

/** GET /api/auth/invite/{kod} (sunucu 81844c5). */
interface InviteCheck {
  valid: boolean; reason?: "used" | "expired" | "unknown"; message?: string;
  kind?: "book" | "account"; book?: string; role?: string; role_label?: string; expires_at?: string;
}

// Mağaza bağlantıları. Android paket adı mobil projeden (capacitor.config.json appId); App Store kimliği bilinmiyor,
// o yüzden iOS'ta mağaza araması açılır — kimlik belli olunca APP_STORE değişmeli.
const APP_SCHEME = "hesapkitap";
const PLAY_STORE = "https://play.google.com/store/apps/details?id=com.alameddinc.hesapkitap";
const APP_STORE = "https://apps.apple.com/tr/search?term=HesapKitap";

const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
const isIOS = /iPhone|iPad|iPod/i.test(ua);
const isAndroid = /Android/i.test(ua);
const inApp = /HesapKitapApp\//.test(ua);

/** "Ayşe" → "Ayşe'nin", "Ali" → "Ali'nin", "Mert" → "Mert'in" (iyelik eki, ünlü uyumu). */
export function possessive(name: string): string {
  const v = [...name.toLocaleLowerCase("tr")].reverse().find((c) => "aeıioöuü".includes(c)) ?? "e";
  const vowelEnd = "aeıioöuü".includes(name.slice(-1).toLocaleLowerCase("tr"));
  const suf = { a: "ın", ı: "ın", e: "in", i: "in", o: "un", u: "un", ö: "ün", ü: "ün" }[v] ?? "in";
  return `${name}'${vowelEnd ? "n" : ""}${suf}`;
}

/** Kayıt ekranında davet önizlemesi: "<Ad>'ın defterine <rol> olarak katılacaksın" ya da geçersizse sunucunun nedeni.
 *  Telefon tarayıcısında üstte "Uygulamada aç" (derin bağlantı) ve mağaza bağlantıları. */
export default function InvitePreview({ code }: { code: string }) {
  const c = code.trim();
  const q = useQuery({ queryKey: ["invite-check", c], queryFn: () => send<InviteCheck>(`/api/auth/invite/${encodeURIComponent(c)}`),
    enabled: c.length >= 6, retry: false, staleTime: 60_000 });
  const d = q.data;
  return (
    <div className="mb-3 space-y-2">
      {(isIOS || isAndroid) && !inApp && c && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm dark:border-blue-900 dark:bg-blue-950/40">
          <div className="flex items-start gap-2">
            <Smartphone size={16} className="mt-0.5 shrink-0 text-blue-700 dark:text-blue-300" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-blue-900 dark:text-blue-100">HesapKitap uygulaması yüklüyse orada aç</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <a className="btn-primary px-3 py-1 text-xs" href={`${APP_SCHEME}://kayit?kod=${encodeURIComponent(c)}`}>Uygulamada aç</a>
                <a className="btn border border-blue-300 bg-white px-3 py-1 text-xs text-blue-800 dark:border-blue-800 dark:bg-transparent dark:text-blue-200"
                  href={isIOS ? APP_STORE : PLAY_STORE} target="_blank" rel="noreferrer">{isIOS ? "App Store'dan indir" : "Google Play'den indir"}</a>
              </div>
              <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">Uygulamayı kurduktan sonra bu bağlantıyı yeniden aç; ya da aşağıdan burada hesap aç.</p>
            </div>
          </div>
        </div>
      )}
      {q.isLoading && <p className="text-xs text-stone-500">Davet kontrol ediliyor…</p>}
      {q.error && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
      {d && !d.valid && (
        <p className="flex gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300" role="alert">
          <XCircle size={16} className="mt-0.5 shrink-0" aria-hidden />{d.message || "Bu davet geçerli değil"}</p>
      )}
      {d?.valid && d.kind === "book" && (
        <p className="flex gap-2 rounded-lg bg-green-50 p-2 text-sm text-green-900 dark:bg-green-950/40 dark:text-green-200" role="status">
          <Users2 size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span><b>{possessive(d.book || "Davet eden")}</b> defterine <b>{d.role_label ?? "üye"}</b> olarak katılacaksın.
            {d.expires_at && <span className="block text-xs opacity-80">Davet {dateTR(d.expires_at)} tarihine kadar geçerli.</span>}</span>
        </p>
      )}
      {d?.valid && d.kind === "account" && (
        <p className="rounded-lg bg-green-50 p-2 text-sm text-green-900 dark:bg-green-950/40 dark:text-green-200" role="status">
          Davet geçerli: kendi hesabını açacaksın.{d.expires_at && ` Son gün ${dateTR(d.expires_at)}.`}</p>
      )}
    </div>
  );
}
