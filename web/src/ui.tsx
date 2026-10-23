import { createContext, useContext } from "react";
import type { BillDraft, Payable, Row } from "./api";
import type { RecurringMonth } from "./components/RecurringMonthForm";

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
  legal_name?: string | null; // fişteki resmi unvan (ekstrede bu ad geçer)
  items?: { name: string; product?: string | null; qty?: number; unit?: string; amount: number | null; split?: string | null }[]; // fiş satırları
  location?: string | null; // fişteki işyeri adresi
  branch?: string | null; // fişteki şube/semt
  wallet_hint?: string | null; // fişte yazan yemek kartı markası (Multinet…)
}

export interface UI {
  month: string;
  setMonth: (m: string) => void;
  go: (route: Route) => void;
  openAdd: () => void;
  openEntry: (row?: Row, draft?: EntryDraft) => void;
  openCardTxn: (row: Row) => void;
  openRecurring: (id?: number) => void;
  openBill: (draft?: BillDraft) => void;
  openPay: (p: Payable) => void;
  openRecurringMonth: (r: RecurringMonth) => void;
  toast: (msg: string) => void;
}

export type Route = "ozet" | "islemler" | "plan" | "ekstreler" | "kurallar" | "ayarlar" | "tasarruf" | "tablo" | "transferler" | "borclar" | "fiyatlar" | "hesaplar" | "zekat" | "diger";

export const UIContext = createContext<UI | null>(null);

export function useUI() {
  const ui = useContext(UIContext);
  if (!ui) throw new Error("UIContext yok");
  return ui;
}
