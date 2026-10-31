import { Landmark } from "lucide-react";
import type { Row } from "../api";

/** Kullanıcının kendi notu: sunucu note_user verirse o (ham banka satırı hariç), eski sunucuda note. */
export const userNote = (r: Pick<Row, "note"> & { note_user?: string | null }) =>
  "note_user" in r ? r.note_user ?? null : r.note;

/** Düzenleme penceresinde "Banka açıklaması": dökümdeki ham metin, kesilmeden; değiştirilemez (not ayrı tutulur). */
export function BankText({ raw }: { raw: string }) {
  return (
    <div className="mt-2 rounded-lg bg-stone-100 p-2.5 text-xs dark:bg-stone-800">
      <div className="mb-0.5 flex items-center gap-1.5 font-medium text-stone-600 dark:text-stone-300">
        <Landmark size={13} aria-hidden /> Banka açıklaması</div>
      <p className="font-mono break-words whitespace-pre-wrap text-stone-700 [overflow-wrap:anywhere] dark:text-stone-200">{raw}</p>
      <p className="mt-1 text-stone-500 dark:text-stone-400">Dökümde yazan metin; değişmez. Not alanına yazdığın bunun yanında saklanır.</p>
    </div>
  );
}
