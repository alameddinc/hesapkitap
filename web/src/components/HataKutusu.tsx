// Tek hata bileşeni (web; mobil: hesapkitap-mobile src/components/HataKutusu.tsx aynı metin ve davranış): anlaşılır Türkçe
// NEDEN + yapılabilecek ADIM + [Tekrar dene] + [Yardım al]. Metin lib/hataAcikla.ts'ten (sunucunun Türkçe metni; teknik/
// İngilizce metin asla gösterilmez). "Yardım al" lib/destek.ts → yardimAl({ nerede, kod, ayrinti }) (destek sohbeti).
//   tur "satir" (varsayılan): sayfa içi kutu (sorgu hatası)
//   tur "tam": ortalanmış büyük kutu (sayfa çökmesi, okunamadı)
// SorguDurumu (HataNotu), Toast (hata) ve PageBoundary bunu ya da parçalarını kullanır.
import { AlertTriangle, MessageCircleQuestion } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "../format";
import { yardimAl } from "../lib/destek";
import { hataAcikla, type HataAciklama } from "../lib/hataAcikla";

// ekran yolu → okunur ad ("Yardım al" bağlamı: nerede); App.tsx NAV etiketleriyle aynı
const ADLAR: Record<string, string> = {
  ozet: "Özet", islemler: "İşlemler", plan: "Planlama", ekstreler: "Ekstreler", kurallar: "Kurallar", ayarlar: "Ayarlar",
  tasarruf: "Tasarruf", tablo: "Tablo", transferler: "Transferler", borclar: "Borç / Alacak", fiyatlar: "Fiyatlar",
  hesaplar: "Hesaplar", zekat: "Zekât ve Sadaka", istatistik: "İstatistik", faturalar: "Faturalar", diger: "Diğer",
  kategorisiz: "Kategorisizler", yonetici: "Yönetici", alisveris: "Alışveriş listesi", faiz: "Faiz ve ücretler",
  "borc-plani": "Borç planı", garantiler: "Garantiler", giris: "Giriş", kayit: "Hesap açma",
};
/** Açık ekranın adı (hash yolundan). */
export function ekranAdi(hash: string = typeof location !== "undefined" ? location.hash : ""): string {
  const p = hash.replace(/^#\/?/, "").split(/[/?]/).filter(Boolean);
  return ADLAR[p[0] ?? "ozet"] ?? "Uygulama";
}

/** Yardım al düğmesi (kendi düzeni olan hata yerleri: yükleme satırı, okunamadı). */
export function YardimDugmesi({ nerede, kod, ayrinti, className }: { nerede?: string; kod?: string; ayrinti?: string; className?: string }) {
  return (
    <button type="button" data-testid="yardim-al"
      className={cx("-my-1 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 font-semibold text-blue-700 hover:bg-blue-50 dark:text-blue-300 dark:hover:bg-blue-950/60", className)}
      onClick={() => yardimAl({ nerede: nerede ?? ekranAdi(), kod, ayrinti })}>
      <MessageCircleQuestion size={16} aria-hidden />Yardım al
    </button>
  );
}

export default function HataKutusu({ error, aciklama, nerede, retry, yedek, tur = "satir", ek, retryLabel, className }: {
  error?: unknown;
  /** hazır açıklama (kendi metni olan yerler); verilirse error'dan türetilmez */
  aciklama?: Partial<HataAciklama> & { neden: string };
  /** "Yardım al" bağlamı; verilmezse açık ekranın adı */
  nerede?: string;
  retry?: () => void;
  /** sunucunun metni gösterilemezse ekrana özgü cümle */
  yedek?: string;
  tur?: "satir" | "tam";
  ek?: ReactNode;
  retryLabel?: string;
  className?: string;
}) {
  const a: HataAciklama = aciklama ? { adim: "", kod: "ekran", tekrar: !!retry, ...aciklama } : hataAcikla(error, yedek);
  const yer = nerede ?? ekranAdi();
  const tam = tur === "tam";
  return (
    <div role="alert" className={cx(tam
      ? "card mx-auto flex max-w-md flex-col items-center gap-3 p-6 text-center"
      : "flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-950 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-50",
    className)}>
      <span className={cx("flex shrink-0 items-center justify-center rounded-full",
        tam ? "size-12 bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" : "mt-0.5 text-amber-700 dark:text-amber-300")}>
        <AlertTriangle size={tam ? 24 : 18} aria-hidden />
      </span>
      <div className={cx("min-w-0 flex-1 space-y-1", tam && "space-y-1.5")}>
        <p className={cx("font-semibold [overflow-wrap:anywhere]", tam ? "text-lg" : "text-sm")}>{a.neden}</p>
        {a.adim && <p className={cx("[overflow-wrap:anywhere]", tam ? "text-sm text-stone-600 dark:text-stone-300" : "text-sm text-amber-900/80 dark:text-amber-100/80")}>{a.adim}</p>}
        {ek}
        <div className={cx("flex flex-wrap gap-2 pt-1.5", tam && "justify-center pt-3")}>
          {retry && (a.tekrar || aciklama) && (
            <button type="button" className={tam ? "btn-primary" : "btn-ghost -ml-2 min-h-9 font-semibold text-amber-950 dark:text-amber-50"} onClick={retry}>
              {retryLabel ?? "Tekrar dene"}</button>
          )}
          <button type="button" data-testid="yardim-al"
            className={tam ? "btn-ghost inline-flex items-center gap-1.5 border border-stone-300 dark:border-stone-700"
              : "inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-blue-700 hover:bg-white/60 dark:text-blue-300 dark:hover:bg-black/20"}
            onClick={() => yardimAl({ nerede: yer, kod: a.kod, ayrinti: a.ayrinti })}>
            <MessageCircleQuestion size={16} aria-hidden />Yardım al
          </button>
        </div>
      </div>
    </div>
  );
}

/** Form içi sunucu hatası (tek satır): süzülmüş metin + "Yardım al". Metin önceden hataMetni ile hazırlanmış olmalı. */
export function HataSatiri({ metin, nerede, kod, ayrinti, className }: { metin: string; nerede?: string; kod?: string; ayrinti?: string; className?: string }) {
  return (
    <div role="alert" className={cx("flex flex-wrap items-start gap-x-2 gap-y-1 text-sm text-red-700 dark:text-red-300", className)}>
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{metin}</span>
      <YardimDugmesi nerede={nerede} kod={kod} ayrinti={ayrinti ?? metin} className="text-sm" />
    </div>
  );
}
