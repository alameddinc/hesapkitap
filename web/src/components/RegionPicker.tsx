// Bölgem (il): resmî fiyatların hangi ilden gösterileceği. Sunucu meta.ui.region verirse (yetenek price_region) görünür;
// PUT /api/me/ui {region}. Mobil karşılığı: hesapkitap-mobile Ayarlar → Tercihler.
import { Check, MapPin, Search } from "lucide-react";
import { useState } from "react";
import { cx } from "../format";
import { searchProvinces } from "../lib/provinces";
import { useUI } from "../ui";
import { useUiPrefs } from "./hooks";
import Modal from "./Modal";

export const REGION_HINT = "Migros ve CarrefourSA fiyatları ile göre değişir; BİM, A101, ŞOK her yerde aynı.";
export const REGION_PROMPT = "Bölgeni seç, sana yakın fiyatları gösterelim";

/** Aranabilir il listesi (81 il) + "Seçimi kaldır". */
export function RegionPicker({ onClose }: { onClose: () => void }) {
  const ui = useUI();
  const { ui: prefs, save } = useUiPrefs();
  const [q, setQ] = useState("");
  const list = searchProvinces(q);
  const cur = prefs.region ?? null;
  const pick = (region: string | null) =>
    save.mutate({ region }, { onSuccess: () => { ui.toast(region ? `Bölgen: ${region}` : "Bölge seçimi kaldırıldı"); onClose(); }, onError: (e) => ui.error(e.message) });
  return (
    <Modal title="Bölgem (il)" onClose={onClose}>
      <div className="space-y-3" data-testid="bolge-secici">
        <p className="text-xs text-stone-500">{REGION_HINT}</p>
        <label className="relative block">
          <span className="sr-only">İl ara</span>
          <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" aria-hidden />
          <input autoFocus type="search" className="input w-full pl-9" placeholder="İl ara (ör. İzmir)" value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && list.length === 1) pick(list[0]!); }} />
        </label>
        <ul className="max-h-[50dvh] divide-y divide-stone-100 overflow-y-auto rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
          {list.map((p) => (
            <li key={p}>
              <button type="button" disabled={save.isPending} onClick={() => pick(p)}
                className={cx("flex w-full items-center justify-between px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/60", p === cur && "font-semibold text-blue-700 dark:text-blue-300")}>
                {p}{p === cur && <Check size={16} aria-label="seçili" />}
              </button>
            </li>
          ))}
          {!list.length && <li className="px-3 py-3 text-stone-500">Bu adla il yok.</li>}
        </ul>
        {cur && <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={save.isPending} onClick={() => pick(null)}>Seçimi kaldır</button>}
      </div>
    </Modal>
  );
}

/** Ayarlar satırı: "Bölgem (il)" + seçili il; eski sunucuda (meta.ui.region yok) görünmez. */
export function RegionForm() {
  const { ui: prefs } = useUiPrefs();
  const [open, setOpen] = useState(false);
  if (!("region" in prefs)) return null;
  return (
    <section className="card p-4">
      <button type="button" className="flex w-full items-start gap-2 text-left text-sm" onClick={() => setOpen(true)} data-testid="bolgem">
        <MapPin size={18} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
        <span className="min-w-0 flex-1"><span className="font-semibold">Bölgem (il)</span>
          <span className="block text-xs text-stone-500">{REGION_HINT}</span></span>
        <span className={cx("shrink-0 text-sm", prefs.region ? "font-medium" : "text-blue-700 dark:text-blue-300")}>{prefs.region || "Seç"}</span>
      </button>
      {open && <RegionPicker onClose={() => setOpen(false)} />}
    </section>
  );
}

/** "Nereden alınır" kartında resmî fiyat varken bölge seçilmemişse tek satır öneri (seçiciyi açar). */
export function RegionPrompt({ show, className }: { show: boolean; className?: string }) {
  const { ui: prefs } = useUiPrefs();
  const [open, setOpen] = useState(false);
  if (!show || !("region" in prefs) || prefs.region) return null;
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} data-testid="bolge-oneri"
        className={cx("flex w-full items-center gap-1.5 text-left text-xs font-medium text-blue-700 hover:underline dark:text-blue-300", className)}>
        <MapPin size={14} className="shrink-0" aria-hidden />{REGION_PROMPT}
      </button>
      {open && <RegionPicker onClose={() => setOpen(false)} />}
    </>
  );
}
