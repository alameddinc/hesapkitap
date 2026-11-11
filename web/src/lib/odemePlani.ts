// Ödeme planı borcun alt kaydı (PUT / DELETE /api/debts/{id}/schedule; yetenek debt_schedule_edit). Borç elden borç
// olarak girilip plan sonradan eklenebilir. Hesap sunucuyla aynı (app/debt_schedule.py): ilk taksit ayından önce ödenen
// plana girmez, taksitler kalan üzerinden kurulur, son taksit farkı alır. Mobil src/lib/odemePlani.ts aynı dosya.
// Uygulama planı yalnız kaydeder; hiçbir metin plan yapmayı önermez.

export interface OdemeSatiri { amount: number; paid_at?: string | null }

const r2 = (n: number) => Math.round(n * 100) / 100;

/** İlk taksit ayından önce ödenen toplam (plana sayılmaz). */
export function planOncesiOdenen(payments: OdemeSatiri[], startMonth: string): number {
  const ilk = `${startMonth}-01`;
  // formda ilk taksit ayı değiştikçe önizleme; kaydedilen planın rakamları sunucudan (schedule.total, paid_before)
  return r2(payments.reduce((s, p) => s + ((p.paid_at ?? "").slice(0, 10) < ilk ? p.amount || 0 : 0), 0)); // parity-ok: GİRDİ
}

/** Planın kapsayacağı tutar: borç toplamı eksi ilk taksit ayından önce ödenen. */
export const planKalani = (amount: number, payments: OdemeSatiri[], startMonth: string) =>
  Math.max(0, r2(amount - planOncesiOdenen(payments, startMonth)));

/** Öneri: kalan ÷ taksit sayısı, tam liraya yuvarlı (kullanıcı değiştirebilir). */
export const oneriTaksit = (kalan: number, sayi: number) => (sayi > 0 && kalan > 0 ? Math.round(kalan / sayi) : 0);

export interface PlanFarki {
  toplam: number; kalan: number; fark: number; sayi: number;
  /** gerçekte kaç taksit olur (taksitler kalanı erken kapatırsa sayıdan az) ve son taksit */
  adet: number; son: number;
}

/** Taksit × sayı kalandan farklıysa bilgi (zorlama yok: kayıt yine yapılır). Eşitse null. */
export function planFarki(kalan: number, taksit: number, sayi: number): PlanFarki | null {
  if (!(kalan > 0) || !(taksit > 0) || !(sayi >= 1)) return null;
  const toplam = r2(taksit * sayi);
  if (Math.abs(toplam - kalan) < 0.01) return null;
  const adet = Math.min(sayi, Math.max(1, Math.ceil(r2(kalan / taksit))));
  const son = r2(kalan - taksit * (adet - 1));
  return { toplam, kalan, fark: r2(toplam - kalan), sayi, adet, son };
}

/** Nötr bilgi: "Taksitler toplamı ₺12.000, kalan borç ₺11.500. Son taksit ₺1.500 sayılır." */
export function planFarkMetni(f: PlanFarki, tl: (n: number) => string): string {
  const bas = `Taksitler toplamı ${tl(f.toplam)}, kalan borç ${tl(f.kalan)}.`;
  return f.adet < f.sayi
    ? `${bas} Bu taksitlerle borç ${f.adet}. taksitte biter; son taksit ${tl(f.son)} sayılır.`
    : `${bas} Son taksit ${tl(f.son)} sayılır.`;
}
