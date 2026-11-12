import { BadgeCheck } from "lucide-react";
import { isViewer, routeVisible, useBook, useCap, useMeta } from "./hooks";

/** Kayıt / kart harcaması ayrıntısında "Garantiye ekle" (sunucu yeteneği "warranty"): Garantiler'de form, kayıttan dolu
 *  gelir (ad, fiyat, tarih, mağaza; fişin kalemleri ve fotoğrafı). Sadece görür üyede ve kapalı sayfada yok. */
export default function GarantiyeEkle({ source, id, onClose }: { source: "entry" | "txn"; id: number; onClose: () => void }) {
  const on = useCap("warranty");
  const book = useBook();
  const meta = useMeta();
  if (!on || isViewer(book.data) || !routeVisible(book.data, "garantiler", meta.data?.modules)) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
      <BadgeCheck size={16} className="shrink-0 text-stone-500" />
      <span className="min-w-0 flex-1">Garantisi olan bir ürün mü?</span>
      <button type="button" className="btn-ghost -my-1 text-blue-700 dark:text-blue-300" data-testid="garantiye-ekle" onClick={() => {
        onClose();
        window.location.hash = `/garantiler?${source}=${id}`;
        window.scrollTo(0, 0);
      }}>Garantiye ekle</button>
    </div>
  );
}
