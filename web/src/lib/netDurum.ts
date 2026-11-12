// Ekranda gösterilen Net durum (Özet, Hesaplar): yeni sunucu (yetenek net_display) /api/net-worth net_display'i verir =
// net − kesilecek kart ekstresi (tahmini). Eski sunucuda alan yok → eski "net" (kesilecek ekstre düşülmez).
// Rakam sunucuda; istemci yalnız hangisini göstereceğini seçer. Mobil src/lib/netDurum.ts ile aynı metinler.

export const NET_KESILECEK = "kesilecek kart ekstresi dahil";

interface NetAlanlari { net: number; net_display?: number; card_estimated?: number }

/** value: gösterilecek rakam; kesilecek: alt metin "kesilecek kart ekstresi dahil" yazılsın mı; yeni: yeni tanım. */
export function netDurumu(d: NetAlanlari, yetenek: boolean) {
  const yeni = yetenek && typeof d.net_display === "number";
  return { value: yeni ? d.net_display! : d.net, kesilecek: yeni && (d.card_estimated ?? 0) > 0, yeni };
}
