import { createContext, useContext } from "react";
import type { BillDraft, Payable, Row } from "./api";

export interface EntryDraft {
  kind?: "gelir" | "gider";
  description?: string;
  amount?: number;
  entry_date?: string;
  category?: string;
  note?: string;
  recurring?: boolean;
  payment?: "kredi_karti" | "nakit" | "havale";
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
  toast: (msg: string) => void;
}

export type Route = "ozet" | "islemler" | "plan" | "ekstreler" | "kurallar" | "ayarlar" | "tasarruf" | "tablo" | "diger";

export const UIContext = createContext<UI | null>(null);

export function useUI() {
  const ui = useContext(UIContext);
  if (!ui) throw new Error("UIContext yok");
  return ui;
}
