// Fiş listesi (muhasebeci için) dönemi: GET /api/receipts/export?period=month|year&value=… ya da ?from&to.
export type ReceiptPick = "this_month" | "last_month" | "this_year" | "last_year" | "range";

export const RECEIPT_PICKS: { key: ReceiptPick; label: string }[] = [
  { key: "this_month", label: "Bu ay" }, { key: "last_month", label: "Geçen ay" }, { key: "this_year", label: "Bu yıl" },
  { key: "last_year", label: "Geçen yıl" }, { key: "range", label: "Tarih aralığı" },
];

/** today "YYYY-MM-DD" → uç sorgusu (format hariç). */
export function receiptQuery(pick: ReceiptPick, today: string, range?: { from: string; to: string }): string {
  const y = Number(today.slice(0, 4));
  const m = Number(today.slice(5, 7));
  if (pick === "this_year") return `period=year&value=${y}`;
  if (pick === "last_year") return `period=year&value=${y - 1}`;
  if (pick === "range") return `from=${range?.from ?? ""}&to=${range?.to ?? ""}`;
  const [yy, mm] = pick === "this_month" ? [y, m] : m === 1 ? [y - 1, 12] : [y, m - 1];
  return `period=month&value=${yy}-${String(mm).padStart(2, "0")}`;
}

export const receiptsExportUrl = (pick: ReceiptPick, today: string, format: "xlsx" | "csv", range?: { from: string; to: string }) =>
  `/api/receipts/export?${receiptQuery(pick, today, range)}&format=${format}`;
