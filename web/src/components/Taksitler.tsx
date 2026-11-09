import { useQuery } from "@tanstack/react-query";
import { ChevronRight, CreditCard, Layers } from "lucide-react";
import { useState } from "react";
import { api, type Summary } from "../api";
import { cx, tl } from "../format";
import {
  TAKSIT_NOT, taksitBaslik, taksitDurum, taksitGoster, taksitKalan, taksitKalemAlt, taksitKisaAlt, taksitSayi,
} from "../lib/taksit";
import { useUI } from "../ui";
import Modal from "./Modal";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

/** Özet → harcamanın altında "Bu ay ödenecek taksitler · ₺4.250 · 6 taksit" (mobil Ozet.tsx TaksitSatiri ile aynı).
 *  Rakam harcamaya EKLENMEZ (in_spend zaten harcamanın içinde); dokununca dönemin taksit listesi açılır. */
export default function TaksitSatiri({ summary }: { summary: Summary }) {
  const o = summary.installments;
  const [open, setOpen] = useState(false);
  if (!taksitGoster(o)) return null;
  const sayi = taksitSayi(o);
  return (
    <>
      <button data-testid="taksit-satiri" onClick={() => setOpen(true)}
        className="card mt-3 flex w-full items-center gap-3 p-3 text-left text-sm active:bg-stone-50 dark:active:bg-stone-800">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300">
          <Layers size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{taksitBaslik(o)}</span>
            <span className="tabular-nums"><b>{tl(o.total)}</b>{sayi && <span className="text-stone-500"> · {sayi}</span>}</span>
          </span>
          {/* kısa: "₺35.487 ekstre bekleniyor"; uzun açıklama ve kalan taksit borcu listede */}
          <span className="block text-xs text-stone-500 tabular-nums">{taksitKisaAlt(o, tl)}</span>
        </span>
        <ChevronRight size={16} className="shrink-0 text-stone-400" />
      </button>
      {open && <TaksitListe month={o.month} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Dönemin taksitleri; her satır dokununca o kaydın düzenlemesi (kart işlemi ya da elle kayıt). */
function TaksitListe({ month, onClose }: { month: string; onClose: () => void }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["installments", month], queryFn: () => api.installments(month) });
  const d = q.data;
  const kalan = d ? taksitKalan(d, tl) : null;
  return (
    <Modal title={d ? taksitBaslik(d) : "Taksitler"} onClose={onClose}>
      <SorguDurumu q={q} etiket="Taksitler yükleniyor"><SatirIskelet n={3} kart={false} /></SorguDurumu>
      {d && (
        <div className="space-y-3">
          <p className="text-sm text-stone-600 dark:text-stone-400">
            <b className="text-stone-900 tabular-nums dark:text-stone-100">{tl(d.total)}</b>
            {taksitSayi(d) && ` · ${taksitSayi(d)}`} · {taksitDurum(d, tl)}
          </p>
          {d.items.length === 0 && <p className="py-4 text-center text-sm text-stone-500">Bu dönemde taksit yok.</p>}
          {d.items.length > 0 && (
            <ul className="-mx-4 divide-y divide-stone-100 border-y border-stone-100 dark:divide-stone-800 dark:border-stone-800">
              {d.items.map((i) => (
                <li key={i.uid}>
                  <button className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
                    onClick={() => {
                      onClose();
                      if (i.row.source === "manual") ui.openEntry(i.row);
                      else ui.openCardTxn(i.row);
                    }}>
                    <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-stone-100 text-stone-500 dark:bg-stone-800">
                      {i.source === "manual" ? <span className="text-xs font-semibold">₺</span> : <CreditCard size={15} />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-start justify-between gap-2">
                        <span className="min-w-0 font-medium break-words">{i.title}</span>
                        <span className={cx("shrink-0 font-semibold tabular-nums", i.amount < 0 && "text-green-700 dark:text-green-400")}>{tl(i.amount)}</span>
                      </span>
                      <span className={cx("mt-0.5 block text-xs break-words", i.in_spend ? "text-stone-500" : "text-blue-700 dark:text-blue-300")}>
                        {taksitKalemAlt(i)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {kalan && <p className="text-sm font-medium tabular-nums">{kalan}</p>}
          <p className="text-xs text-stone-500">{TAKSIT_NOT}</p>
        </div>
      )}
    </Modal>
  );
}
