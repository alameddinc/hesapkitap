// Kayıtlı giderin "Ürünler" bölümü (kayıt düzenleme penceresi): GET /api/entries/{id}/receipt-items; düzenlenince
// "Kalemleri kaydet" → PUT aynı yol (kalemleri tamamen değiştirir; kaydın tutarı, kategorisi, bölmesi değişmez).
// "Fişten yeniden oku": POST /api/entries/{id}/receipt-reread — sonuç karşılaştırılır, seçilirse yine PUT ile yazılır.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api";
import type { ReceiptItem } from "./check";
import ReceiptItems from "./ReceiptItems";

export default function SavedReceiptItems({ entryId, total, receiptFile, readOnly, onToast }: {
  entryId: number; total: number; receiptFile?: string | null; readOnly?: boolean; onToast?: (m: string) => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["receipt-items", entryId], queryFn: () => api.receiptItems(entryId), retry: false });
  const [items, setItems] = useState<ReceiptItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cur = items ?? q.data?.items ?? null;
  const dirty = !!items && !!q.data && JSON.stringify(items) !== JSON.stringify(q.data.items);
  const photo = receiptFile ? `/api/entries/${entryId}/receipt` : null;

  if (q.isLoading) return <p className="text-xs text-stone-500">Fiş kalemleri yükleniyor…</p>;
  // kalem yok ve fotoğraf yok: bölüm gösterilmez; kalem yok ama fotoğraf varsa yeniden okunabilir
  if (q.error || !cur || (!cur.length && !receiptFile)) return null;

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.saveReceiptItems(entryId, cur!);
      qc.setQueryData(["receipt-items", entryId], { entry_id: entryId, items: r.items, check: r.check });
      setItems(null);
      void qc.invalidateQueries({ queryKey: ["prices"] });
      onToast?.(`${r.saved} kalem kaydedildi`);
    } catch (e) {
      setErr((e as Error).message || "Kaydedilemedi");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <ReceiptItems items={cur} total={total} onChange={readOnly ? () => undefined : setItems} photo={photo}
        onReread={receiptFile && !readOnly ? () => api.rereadEntryReceipt(entryId) : undefined}
        note={!cur.length ? "Bu kayıtta kalem yok; fişten yeniden okuyabilir ya da elle ekleyebilirsin." : null} />
      {dirty && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="mr-auto text-xs text-stone-500 dark:text-stone-400">Kalemler değişti; kaydın tutarı değişmez.</span>
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => setItems(null)}>Değişiklikleri geri al</button>
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void save()}>{busy ? "Kaydediliyor…" : "Kalemleri kaydet"}</button>
        </div>
      )}
      {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
    </div>
  );
}
