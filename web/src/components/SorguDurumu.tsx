// Ortak sorgu durumu (UX denetimi 09.11, sistemik düzeltme A; mobil components/Durum.tsx ile aynı davranış ve metin):
//   yükleniyor  → son satırlara benzeyen iskelet; 0,8 sn sonra altında "Yükleniyor…"; 5 sn sonra "Bağlantı yavaş · Bekliyoruz"
//   çevrimdışı  → "İnternet bağlantısı yok · Bağlanınca kendiliğinden yenilenir" (bağlanınca sorgu kendiliğinden yenilenir: main.tsx)
//   hata        → sunucunun metni + "Tekrar dene"; kart sessizce gizlenmez
//   veri geldi  → hiçbir şey çizmez (çağıran veriyi kendisi çizer)
// Kullanım:  <SorguDurumu q={q}><SatirIskelet n={3} /></SorguDurumu>   {q.data && …}
// Birden çok sorgu: q={[a, b]} (biri hatalıysa hata, biri bekliyorsa iskelet).
import { WifiOff } from "lucide-react";
import HataKutusu from "./HataKutusu";
import type { ReactNode } from "react";
import { cx } from "../format";
import { cevrimdisiHata, DURUM, useBekleme, useCevrimici } from "../lib/durum";

export interface SorguBenzeri {
  isLoading: boolean;
  error: unknown;
  data?: unknown;
  fetchStatus?: string;
  refetch: () => unknown;
}

/** Sorgunun verisi henüz yok ve yükleniyor (ya da bağlantı bekleniyor). */
export const bekliyor = (q: SorguBenzeri) => q.data === undefined && !q.error && (q.isLoading || q.fetchStatus === "paused");

export default function SorguDurumu({ q, children, etiket, kompakt = false, className }: {
  q: SorguBenzeri | SorguBenzeri[];
  /** iskelet: son satırlara benzesin; verilmezse 3 satırlık liste iskeleti */
  children?: ReactNode;
  /** ekran okuyucu için ("İşlemler yükleniyor") */
  etiket?: string;
  /** satır içi (küçük bölüm, pencere içi): kısa iskelet, yazı yanında */
  kompakt?: boolean;
  className?: string;
}) {
  const qs = Array.isArray(q) ? q : [q];
  const hatali = qs.find((x) => x.error && x.data === undefined);
  const yukleniyor = !hatali && qs.some(bekliyor);
  const evre = useBekleme(yukleniyor);
  const online = useCevrimici();
  const tekrar = () => { qs.forEach((x) => { if (x.error || x.data === undefined) void x.refetch(); }); };

  if (hatali) {
    if (cevrimdisiHata(hatali.error)) return <Cevrimdisi retry={tekrar} className={className} />;
    return <HataNotu error={hatali.error} retry={tekrar} className={className} />;
  }
  if (!yukleniyor) return null;
  if (!online) return <Cevrimdisi retry={tekrar} className={className} />;
  return (
    <div className={cx(kompakt ? "flex items-center gap-3" : "space-y-2", className)} aria-busy="true" aria-label={etiket}>
      {children ?? (kompakt ? <Sk className="h-4 flex-1" /> : <SatirIskelet n={3} />)}
      <p aria-live="polite" data-evre={evre}
        className={cx("flex min-h-5 items-center gap-2 text-sm text-stone-500 dark:text-stone-400", kompakt && evre === 0 && "hidden", kompakt && "shrink-0")}>
        {evre > 0 && <><Donen />{evre === 2 ? DURUM.yavas : DURUM.yukleniyor}</>}
      </p>
    </div>
  );
}

/** Dönen küçük halka (hareket azaltılmışsa durur). */
export function Donen() {
  return <span aria-hidden className="inline-block size-3.5 shrink-0 animate-spin rounded-full border-2 border-stone-300 border-t-blue-600 motion-reduce:animate-none dark:border-stone-600 dark:border-t-blue-400" />;
}

/** İskelet bloğu. */
export function Sk({ className }: { className?: string }) {
  return <span className={cx("block animate-pulse rounded-md bg-stone-200 motion-reduce:animate-none dark:bg-stone-800", className)} />;
}

/** Liste iskeleti: ikon + iki satır + tutar (İşlemler, Hesaplar, Bütçeler satırlarına benzer). */
export function SatirIskelet({ n = 3, ikon = true, kart = true }: { n?: number; ikon?: boolean; kart?: boolean }) {
  return (
    <div className={cx(kart && "card", "divide-y divide-stone-100 dark:divide-stone-800")}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3">
          {ikon && <Sk className="size-9 shrink-0 rounded-lg" />}
          <span className="min-w-0 flex-1 space-y-1.5"><Sk className="h-3.5 w-3/5" /><Sk className="h-3 w-2/5" /></span>
          <Sk className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

/** Kart iskeleti: başlık + büyük rakam + satırlar (Özet kartları, Zekât, Planlama). */
export function KartIskelet({ satir = 2, className }: { satir?: number; className?: string }) {
  return (
    <div className={cx("card space-y-2.5 p-4", className)}>
      <Sk className="h-3 w-28" /><Sk className="h-7 w-44" />
      {Array.from({ length: satir }, (_, i) => <Sk key={i} className="h-3.5 w-full" />)}
    </div>
  );
}

/** Çevrimdışı kutusu: bağlanınca kendiliğinden yenilenir; yine de elle "Tekrar dene". */
export function Cevrimdisi({ retry, className }: { retry?: () => void; className?: string }) {
  return (
    <div role="status" className={cx("flex items-center gap-2.5 rounded-xl bg-stone-100 px-4 py-3 text-sm text-stone-700 dark:bg-stone-800/70 dark:text-stone-200", className)}>
      <WifiOff size={18} className="shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">{DURUM.cevrimdisi}</span>
      {retry && <button className="btn-ghost -my-1 min-h-11 font-semibold" onClick={retry}>{DURUM.tekrar}</button>}
    </div>
  );
}

/** Hata kutusu: tek hata bileşeni (HataKutusu: neden + adım + Tekrar dene + Yardım al; teknik metin gösterilmez). */
export function HataNotu({ error, retry, className, nerede, yedek }: { error: unknown; retry?: () => void; className?: string; nerede?: string; yedek?: string }) {
  return <HataKutusu error={error} retry={retry} className={className} nerede={nerede} yedek={yedek} />;
}

/** Kabuk şeridi: bağlantı yokken üst çubuğun altında ince "Çevrimdışı · son görülen veri" (akışta; içeriği örtmez). */
export function CevrimdisiSerit({ className }: { className?: string }) {
  const online = useCevrimici();
  if (online) return null;
  return (
    <div role="status" className={cx("flex items-center justify-center gap-1.5 bg-stone-900 px-3 py-1 text-xs font-semibold text-white dark:bg-stone-100 dark:text-stone-900", className)}>
      <WifiOff size={13} aria-hidden />Çevrimdışı · son görülen veri
    </div>
  );
}
