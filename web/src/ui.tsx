import { createContext, useContext } from "react";
import type { BillDraft, Payable, Row } from "./api";
import type { ReceiptCheck } from "./components/receipt/check";
import type { TaxInfo } from "./components/receipt/TaxCard";
import type { RecurringMonth } from "./components/RecurringMonthForm";
import type { DekontResult } from "./components/TransferForm";

export interface EntryDraft {
  kind?: "gelir" | "gider";
  description?: string;
  amount?: number;
  entry_date?: string;
  category?: string;
  note?: string;
  recurring?: boolean;
  gross?: boolean; // maaş: brüt seçeneği açık gelsin
  payment?: "kredi_karti" | "nakit" | "havale" | "kart";
  installments?: number; // kredi kartı taksit sayısı (fişte yazıyorsa)
  card_last4?: string | null; // fişte yazan kart numarasının son 4 hanesi
  legal_name?: string | null; // fişteki resmi unvan (ekstrede bu ad geçer)
  receipt_file?: string | null; // saklanan fiş fotoğrafı (okuma yanıtından; sunucu doğrular)
  items?: { name: string; product?: string | null; qty?: number; unit?: string; amount: number | null; split?: string | null; unit_price?: number | null }[]; // fiş satırları
  photo_url?: string | null; // önizleme: okunan fotoğrafın yerel adresi (object URL)
  reread_used?: boolean; // sunucu fişi ikinci kez, güçlü modelle okudu ve onu kullandı
  location?: string | null; // fişteki işyeri adresi
  branch?: string | null; // fişteki şube/semt
  wallet_hint?: string | null; // fişte yazan yemek kartı markası (Multinet…)
  cached?: boolean; // aynı fiş daha önce okunmuştu: okuma hakkı düşmedi
  check?: ReceiptCheck | null; // okumanın denetimi (sunucu): karışan rakam önerileri (yetenek digit_fix)
  /** yetenek receipt_tax: okunan KDV / VKN / vergi dairesi (anahtar varsa sunucu biliyor; kart gösterilir) */
  tax?: TaxInfo;
  /** yetenek total_check: "kontrol" → toplam fiş metninde bulunamadı, tutar vurgulanır */
  total_check?: "ok" | "kontrol" | null;
}

export interface ToastOptions {
  /** işlemi geri alan istek; başarıyla bitince "Geri alındı" denir */
  undo?: () => Promise<unknown>;
  /** ek düğme (ör. "Aç": kaydın gittiği deftere geç) */
  action?: { label: string; run: () => void };
}

export interface UI {
  month: string;
  setMonth: (m: string) => void;
  go: (route: Route) => void;
  openAdd: () => void;
  openEntry: (row?: Row, draft?: EntryDraft) => void;
  openCardTxn: (row: Row) => void;
  openRecurring: (id?: number, row?: Row) => void; // row: listedeki ayın satırı (kim harcadı seçimi)
  openBill: (draft?: BillDraft) => void;
  openPay: (p: Payable) => void;
  openRecurringMonth: (r: RecurringMonth) => void;
  /** Transfer ekle (elle ya da okunmuş dekontla; yetenek dekont_parse) */
  openTransfer: (dekont?: DekontResult | null) => void;
  /** Başarı/bilgi bildirimi: süre metnin uzunluğuna göre (en az 3 sn); undo/action verilirse düğme (en az 8 sn). */
  toast: (msg: string, opts?: ToastOptions) => void;
  /** Hata bildirimi: kırmızı, ekran okuyucuya alert; kendiliğinden kapanmaz, kullanıcı kapatır. */
  error: (msg: string) => void;
}

export type Route = "ozet" | "islemler" | "plan" | "ekstreler" | "kurallar" | "ayarlar" | "tasarruf" | "tablo" | "transferler" | "borclar" | "fiyatlar" | "hesaplar" | "zekat" | "istatistik" | "faturalar" | "diger" | "kategorisiz" | "yonetici" | "alisveris" | "faiz";

export const UIContext = createContext<UI | null>(null);

export function useUI() {
  const ui = useContext(UIContext);
  if (!ui) throw new Error("UIContext yok");
  return ui;
}
