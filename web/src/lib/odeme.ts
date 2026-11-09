// Ödemeler: liste anahtarı ve kart ekstresinin hazır tutarları (Tamamı / Asgari / Başka tutar). Mobil:
// lib/endpoints/planlama.ts payableKey, components/planlama/OdemeSheet.tsx ekstreSecenek / kalanNotu (aynı kural, aynı cümle).

type Kalem = { key?: string; type: string; id: number | null; title: string; due_date: string; amount: number;
  min_payment: number | null; paid_amount: number | null };

/** Sunucunun key'i (payables_key) varsa o; yoksa tür + id + başlık + tarih. Eski "tür-id-tarih" anahtarı iki kartın
 *  tahmininde (id yok, aynı gün) çakışıyor, React çakışan satırı silmeden bırakıp her yeniden sıralamada biriktiriyordu. */
export const payableKey = (p: Pick<Kalem, "key" | "type" | "id" | "title" | "due_date">) =>
  p.key ?? `${p.type}-${p.id ?? ""}-${p.title}-${p.due_date}`;

/** Ekstrenin hazır tutarları. Asgari: ekstrede yazıyorsa, borçtan küçükse ve (düzeltme değilse) henüz karşılanmadıysa. */
export function ekstreSecenek(p: Kalem, duzelt = false): { full: number; min: number | null } {
  const min = p.min_payment;
  const ok = min != null && min > 0 && min + 0.01 < p.amount && (duzelt || min > (p.paid_amount ?? 0) + 0.01);
  return { full: p.amount, min: ok ? min : null };
}

export type Secim = "tam" | "asgari" | "diger";

/** Yazılan tutar hangi seçeneğe denk geliyor (Başka tutar seçiliyken hep "diger"). */
export function secimOf(amt: number | null, s: { full: number; min: number | null }, diger: boolean): Secim {
  if (diger || amt == null || !Number.isFinite(amt)) return "diger";
  if (Math.abs(amt - s.full) < 0.01) return "tam";
  if (s.min != null && Math.abs(amt - s.min) < 0.01) return "asgari";
  return "diger";
}

/** Kısmi ödeme notu (mobil ile aynı cümle). tl: para biçimi. */
export const kalanNotu = (rest: string) =>
  `Kalan ${rest} ödenecekler listesinde kalır. Kalanına faiz işleyebilir; son ödeme gününe kadar ödenmezse sonraki ekstreye devreder.`;
