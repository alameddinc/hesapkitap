// Garanti arşivi: tipler ve saf yardımcılar (sunucu yeteneği "warranty"; mobil src/lib/garanti.ts ile aynı sözler).

export type GarantiDurum = "aktif" | "yakinda" | "bitti";
export type BelgeTuru = "fatura" | "garanti" | "diger";

export interface GarantiBelge {
  id: number; doc_type: BelgeTuru; doc_label: string; filename: string | null; size: number | null; pdf: boolean;
  from_receipt: boolean; created_at?: string;
}
export interface Garanti {
  id: number; name: string; brand: string | null; model: string | null; store: string | null; purchase_date: string;
  price: number | null; months: number; extra_months: number; total_months: number; ends_at: string; days_left: number;
  status: GarantiDurum; status_label: string; left_text: string; serial_no: string | null; note: string | null;
  entry_id: number | null; txn_id: number | null; created_by: number | null; files: GarantiBelge[]; can_edit: boolean;
}
export interface GarantiListe {
  items: Garanti[];
  counts: Record<GarantiDurum | "all", number>;
  quota: { limit: number | null; used: number; locked: boolean; need: string | null };
  default_months: number; soon_days: number; max_files: number; doc_types: Record<BelgeTuru, string>;
}
export interface GarantiTaslak {
  source: "entry" | "txn" | "photo"; entry_id: number | null; txn_id: number | null; name: string | null; store: string | null;
  purchase_date: string | null; price: number | null; items: { name: string; qty?: number; amount: number; brand?: string | null }[];
  receipt_file: string | null; total?: number | null; installments?: number | null;
}
export interface KayitliFis { entry_id: number; date: string; store: string; amount: number; items: number; photo: boolean; preview: string[] }
export interface GarantiYaz {
  name: string; brand?: string | null; model?: string | null; store?: string | null; purchase_date: string; price?: number | null;
  months?: number; extra_months?: number; serial_no?: string | null; note?: string | null;
  entry_id?: number | null; txn_id?: number | null; receipt_file?: string | null;
}

/** Fiş okumasının (POST /api/receipts/parse) yanıtı → form taslağı. Kalem tekse ad ve fiyat ondan. */
export function okumadanTaslak(r: { merchant?: string | null; date?: string | null; total?: number | null; receipt_file?: string | null;
  items?: { name?: string; product?: string | null; brand?: string | null; qty?: number; amount?: number | null }[] }): GarantiTaslak {
  const items = (r.items ?? [])
    .filter((i) => (i.product || i.name) && typeof i.amount === "number" && i.amount > 0)
    .map((i) => ({ name: (i.product || i.name)!, qty: i.qty, amount: i.amount as number, brand: i.brand ?? null }));
  const one = items.length === 1 ? items[0] : null;
  return { source: "photo", entry_id: null, txn_id: null, name: one?.name ?? null, store: r.merchant ?? null,
    purchase_date: r.date && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : null, price: one?.amount ?? (items.length ? null : r.total ?? null),
    items, receipt_file: r.receipt_file ?? null, total: r.total ?? null };
}

/** "Arıza / servis" için kopyalanacak özet (servise / mağazaya yazarken). */
export function servisMetni(g: Garanti, tarih: (iso: string) => string, para: (x: number) => string): string {
  const satir = [
    g.name + (g.brand || g.model ? ` (${[g.brand, g.model].filter(Boolean).join(" ")})` : ""),
    g.serial_no ? `Seri no: ${g.serial_no}` : null,
    `Alış: ${tarih(g.purchase_date)}${g.store ? `, ${g.store}` : ""}${g.price != null ? `, ${para(g.price)}` : ""}`,
    `Garanti bitişi: ${tarih(g.ends_at)}${g.extra_months ? ` (${g.extra_months} ay ek garanti dahil)` : ""}`,
  ];
  return satir.filter(Boolean).join("\n");
}

/** Süre seçenekleri (ay). Varsayılan 24: Türkiye'de tüketici ürünlerinde yasal garanti en az 2 yıl. */
export const SURELER = [12, 24, 36, 60] as const;
export const sureYazi = (ay: number) => (ay % 12 === 0 && ay ? `${ay / 12} yıl` : `${ay} ay`);

export const DURUM_RENK: Record<GarantiDurum, string> = {
  aktif: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  yakinda: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  bitti: "bg-stone-200 text-stone-600 dark:bg-stone-800 dark:text-stone-400",
};
