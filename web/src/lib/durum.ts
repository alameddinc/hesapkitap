// Sorgu durumu (UX denetimi 09.11, sistemik düzeltme A): yükleniyor → yavaş → çevrimdışı → hata.
// MOBİLİN AYNASI: hesapkitap-mobile src/lib/durum.ts ile metinler ve süreler birebir aynı; birini değiştirirsen ötekini de değiştir.
import { onlineManager } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";

export const DURUM = {
  yukleniyor: "Yükleniyor…",
  yavas: "Bağlantı yavaş · Bekliyoruz",
  // fiyat/zincir/boy listeleri (alışveriş, Fiyatlar): "Yükleniyor…" yerine bu; yavaş ve çevrimdışı ortak metinle
  fiyatYukleniyor: "Fiyatlar getiriliyor…",
  cevrimdisi: "İnternet bağlantısı yok · Bağlanınca kendiliğinden yenilenir",
  tekrar: "Tekrar dene",
  kaydedilemedi: "Bağlantı yok, kaydedilemedi",
  // bağlantısız kayıt kutusu (lib/bekleyen.ts): elle gider/gelir, transfer, işlem düzenleme
  taslakSaklandi: "İnternet yok · Bağlanınca kaydedeceğiz",
  bekleyen: (n: number) => `${n} kayıt gönderilmeyi bekliyor`,
  gonderildi: (n: number) => (n === 1 ? "Bekleyen kayıt kaydedildi" : `${n} bekleyen kayıt kaydedildi`),
  simdiDene: "Şimdi dene",
  taslakSil: "Taslağı sil",
  // fiş/ekstre okuma bağlantısız: dosya saklanır, bağlanınca tek dokunuşla okunur (okuma hakkı sormadan harcanmaz)
  okumaBekliyor: "Okuma için internet gerekiyor · Fotoğraf saklandı, bağlanınca okunacak",
  okumaBekliyorDosya: "Okuma için internet gerekiyor · Dosya saklandı, bağlanınca okunacak",
  okumaHazir: (n: number) => (n === 1 ? "Saklanan belge okunmayı bekliyor" : `${n} saklanan belge okunmayı bekliyor`),
  simdiOku: "Şimdi oku",
  // kaydedilmemiş değişiklik (lib/kirli.ts)
  kirliBaslik: "Değişiklikler kaydedilmedi",
  kirliMetin: "Kaydetmeden çıkarsan yaptığın değişiklikler kaybolur.",
  kirliKaydet: "Kaydet",
  kirliCik: "Kaydetmeden çık",
  kirliVazgec: "Vazgeç",
  // kayıttan sonra nereye gitti (lib/kayitYeri.ts)
  kaydedildi: (donem: string) => `Kaydedildi · ${donem} dönemi`,
  gor: "İşlemler'de gör",
} as const;

/** "Yükleniyor…" yazısı bu süreden sonra (ms); öncesinde yalnız iskelet. */
export const YAZI_MS = 800;
/** "Bağlantı yavaş" bu süreden sonra (ms). */
export const YAVAS_MS = 5000;

/** Bekleme evresi: 0 = yalnız iskelet, 1 = "Yükleniyor…", 2 = "Bağlantı yavaş". aktif false olunca 0'a döner. */
export function useBekleme(aktif: boolean): 0 | 1 | 2 {
  const [evre, setEvre] = useState<0 | 1 | 2>(0);
  useEffect(() => {
    setEvre(0);
    if (!aktif) return;
    const a = setTimeout(() => setEvre(1), YAZI_MS);
    const b = setTimeout(() => setEvre(2), YAVAS_MS);
    return () => { clearTimeout(a); clearTimeout(b); };
  }, [aktif]);
  return aktif ? evre : 0;
}

/** Bağlantı var mı (TanStack onlineManager: tarayıcının online/offline olayları). */
export const useCevrimici = () => useSyncExternalStore(
  (f) => onlineManager.subscribe(f),
  () => onlineManager.isOnline(),
);

/** Hata çevrimdışılıktan mı: yanıt hiç gelmedi (status 0; lib/apiError send, lib/idem netError → offline bayrağı) ve
 *  bağlantı yok. Sunucu hatası (status > 0) çevrimdışı sayılmaz. */
export function cevrimdisiHata(e: unknown): boolean {
  if (!e || typeof e !== "object") return false;
  const x = e as { status?: number; offline?: boolean; body?: { offline?: boolean } | null };
  if (x.offline || x.body?.offline) return true;
  return x.status === 0 && typeof navigator !== "undefined" && navigator.onLine === false;
}

// ---------------------------------------------------------------- bağlantısız kayıt: "Tekrar dene"
// Çevrimdışıyken yazma hemen düşer (main.tsx networkMode "always": askıda beklemez). Hata nesnesine yeniden deneme işlevi
// bağlanır; ui.hata bu hatayı "Bağlantı yok, kaydedilemedi" + [Tekrar dene] olarak gösterir.
//   - useMutation ile: MutationCache.onError mutasyonu aynı değişkenlerle yeniden çalıştırır (onSuccess de çalışır)
//   - useSave (components/hooks.ts) mutasyonları da MutationCache'ten geçer
const tekrarlar = new WeakMap<object, () => Promise<unknown>>();
export function tekrarBagla(e: unknown, fn: () => Promise<unknown>): void {
  if (e && typeof e === "object") tekrarlar.set(e, fn);
}
export const tekrarFor = (e: unknown): (() => Promise<unknown>) | undefined =>
  e && typeof e === "object" ? tekrarlar.get(e) : undefined;
