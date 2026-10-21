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
  payment?: "kredi_karti" | "nakit" | "havale";
  legal_name?: string | null; // fişteki resmi unvan (ekstrede bu ad geçer)
  items?: { name: string; qty?: number; amount: number | null; split?: string | null }[]; // fiş satırları (kalem kuralları)
  location?: string | null; // fişteki işyeri adresi
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

export type Route = "ozet" | "islemler" | "plan" | "ekstreler" | "kurallar" | "ayarlar" | "tasarruf" | "tablo" | "transferler" | "borclar" | "diger";

export const UIContext = createContext<UI | null>(null);

export function useUI() {
  const ui = useContext(UIContext);
  if (!ui) throw new Error("UIContext yok");
  return ui;
}
