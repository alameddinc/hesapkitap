// Fiş kalemlerinin denetimi ve düzeltme işlemleri. Sunucudaki app/receipt_check.py ile birebir aynı algoritma:
// kullanıcı düzenledikçe istemcide yeniden hesaplanır (sunucunun ilk "check"i yalnız ilk okumayı anlatır).
// Mobil karşılığı: hesapkitap-mobile src/components/fis/kalem.ts.

export interface ReceiptItem {
  id?: number; name: string; product?: string | null; brand?: string | null;
  qty?: number; unit?: string; amount: number | null; unit_price?: number | null; split?: string | null;
}
export interface ItemCheck { index: number; expected: number | null; mismatch: boolean }
export interface Shift { from_index: number; direction: "up" | "down"; amounts: (number | null)[]; items_total: number; diff: number | null }
export interface ReceiptCheck {
  items_total: number; receipt_total: number | null; diff: number | null; ok: boolean;
  items: ItemCheck[]; shift_start: number | null; suggested_shift: Shift | null;
}
/** POST /api/receipts/reread ve /api/entries/{id}/receipt-reread yanıtı (hiçbir şey kaydedilmez). */
export interface Reread { items: ReceiptItem[]; total: number | null; model?: string; confidence?: string; check?: ReceiptCheck; receipt_file?: string }

export const TOL = 0.02;

/** Sunucuyla (app/money.py) aynı yuvarlama: 2 ondalık, yarım yukarı (sıfırdan uzağa), sayının en kısa ondalık
 *  gösterimi üzerinden. Math.round(n * 100) / 100 1,005'i 1,00, -0,125'i -0,12 yapıyordu (sunucu 1,01 / -0,13).
 *  JS toString ile Python repr aynı en kısa gösterimi verir, ikisi aynı sonucu bulur. */
export function money(n: number): number {
  if (!Number.isFinite(n)) return n;
  const a = Math.abs(n);
  if (a < 0.001) return 0; // (toString bu aralıkta üstel gösterime geçebilir; yuvarlanınca zaten 0)
  const [int, frac = ""] = (a >= 1e21 ? a.toFixed(0) : a.toString()).split(".");
  let cents = Number(int) * 100 + Number((frac + "00").slice(0, 2));
  if ((frac[2] ?? "0") >= "5") cents += 1;
  const v = cents / 100;
  return n < 0 && v !== 0 ? -v : v;
}
const r2 = money;

export const expectedOf = (it: ReceiptItem): number | null => {
  const up = it.unit_price, q = it.qty || 1;
  return typeof up === "number" && up > 0 ? r2(q * up) : null;
};
const mismatches = (items: ReceiptItem[], amounts: (number | null)[]) =>
  items.map((it, i) => { const e = expectedOf(it); const a = amounts[i]; return e != null && (a == null || Math.abs(a - e) > TOL); });
const fill = (items: ReceiptItem[], amounts: (number | null)[]) => amounts.map((a, i) => (a != null ? a : expectedOf(items[i])));
const total = (amounts: (number | null)[]) => r2(amounts.reduce<number>((s, a) => s + (typeof a === "number" ? a : 0), 0));

/** Kaydırılmış fiyatlar: "down" = from'dan itibaren her fiyat bir alt satıra iner, "up" = bir üst satıra çıkar.
 *  Açılan yuva adet × birim fiyatla dolar (bilinmiyorsa boş). */
export function shiftAmounts(items: ReceiptItem[], from: number, direction: "up" | "down"): (number | null)[] {
  const amounts = items.map((i) => i.amount);
  const tail = amounts.slice(from);
  const moved = direction === "down" ? [null, ...tail.slice(0, -1)] : [...tail.slice(1), null];
  return fill(items, [...amounts.slice(0, from), ...moved]);
}

export function checkItems(items: ReceiptItem[], receiptTotal: number | null | undefined): ReceiptCheck {
  const rt = receiptTotal ?? null;
  const amounts = items.map((i) => i.amount);
  const mis = mismatches(items, amounts);
  const items_total = total(amounts);
  const diff = rt != null ? r2(rt - items_total) : null;
  const start = mis.findIndex(Boolean);
  let suggested: Shift | null = null;
  if (start >= 0) {
    const score = (a: (number | null)[]) => [mismatches(items, a).filter(Boolean).length, rt != null ? Math.abs(rt - total(a)) : 0] as const;
    const less = (x: readonly [number, number], y: readonly [number, number]) => x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]);
    const base = [mis.filter(Boolean).length, diff != null ? Math.abs(diff) : 0] as const;
    let best: { s: readonly [number, number]; k: number; d: "up" | "down"; c: (number | null)[] } | null = null;
    for (let k = 0; k <= start; k++) {
      for (const d of ["down", "up"] as const) {
        const c = shiftAmounts(items, k, d);
        const s = score(c);
        if (less(s, base) && (!best || less(s, best.s))) best = { s, k, d, c };
      }
    }
    if (best) suggested = { from_index: best.k, direction: best.d, amounts: best.c, items_total: total(best.c), diff: rt != null ? r2(rt - total(best.c)) : null };
  }
  return {
    items_total, receipt_total: rt, diff, ok: !mis.some(Boolean) && (diff == null || Math.abs(diff) <= TOL),
    items: items.map((it, i) => ({ index: i, expected: expectedOf(it), mismatch: mis[i] })),
    shift_start: start >= 0 ? start : null, suggested_shift: suggested,
  };
}

export const withAmounts = (items: ReceiptItem[], amounts: (number | null)[]): ReceiptItem[] =>
  items.map((it, i) => ({ ...it, amount: amounts[i] ?? null }));

/** Bu satırı altındakiyle birleştir (iki satıra bölünmüş ürün: ad bir satırda, fiyat ötekinde). Ad birleşir, tutar
 *  dolu olanınki (ikisi de doluysa toplam), adet büyük olanı. */
export function mergeWithNext(items: ReceiptItem[], i: number): ReceiptItem[] {
  const a = items[i], b = items[i + 1];
  if (!a || !b) return items;
  const amount = a.amount != null && b.amount != null ? r2(a.amount + b.amount) : a.amount ?? b.amount;
  const merged: ReceiptItem = { ...b, ...a, name: `${a.name} ${b.name}`.replace(/\s+/g, " ").trim(), amount,
    qty: Math.max(a.qty || 1, b.qty || 1), unit_price: a.unit_price ?? b.unit_price ?? null, id: a.id ?? b.id };
  return [...items.slice(0, i), merged, ...items.slice(i + 2)];
}

/** Sunucuya giden kalem (PUT /api/entries/{id}/receipt-items, POST /api/entries items). */
export const toServer = (items: ReceiptItem[]) => items.filter((i) => i.name.trim()).map((i) => ({
  name: i.name.trim(), product: i.product ?? null, brand: i.brand ?? null, qty: i.qty || 1, unit: i.unit || "adet",
  amount: i.amount, unit_price: i.unit_price ?? null, ...(i.split ? { split: i.split } : {}),
}));
