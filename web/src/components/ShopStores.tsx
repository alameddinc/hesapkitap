// Mağaza süzgeci (yetenek shop_store_filter): kişi girmek istemediği zincirleri ve yerel marketleri kapatır; kapalı
// mağazalar fiyatlarda ve planlarda hiç görünmez. Kişi başı (ortak defterde herkesin kendi seçimi).
// - GET /api/shopping-list/stores → {hidden, chains [{chain, hidden}], local {hidden, names, count}}
// - PUT /api/me/ui {shop_hidden {chains (tam liste) | local}}
// Sheet her yerden açılır (openShopStores; App'te <ShopStoresHost />): liste ekranının "Mağazalar" düğmesi, Ayarlar
// satırı ve teklif gösterilen her yerdeki "N mağaza gizli · Değiştir" ipucu. Mobil karşılığı: hesapkitap-mobile.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, EyeOff, Store } from "lucide-react";
import { useEffect, useState } from "react";
import { cx } from "../format";
import { send } from "../lib/apiError";
import { applyHidden, hiddenText, localSub, storesButtonText, storesSettingValue, toggleHidden, type ShopHidden, type StoresData } from "../lib/shopStores";
import { useUI } from "../ui";
import { useCap, useMeta } from "./hooks";
import Modal from "./Modal";

export const STORES = ["shop-stores"] as const;
/** Süzgeç değişince tazelenecek her şey (teklif gösterilen yerler). */
const AFFECTED = ["meta", "shopping-list", "shopping-plan", "shopping-optimize", "shopping-sizes", "shop-offers", "price-groups",
  "price-groups-search", "prices"];

let opener: (() => void) | null = null;
/** Mağazalar sayfasını aç (host yoksa sessizce hiçbir şey yapmaz). */
export const openShopStores = () => opener?.();

/** App'te bir kez: openShopStores() isteklerini çizer. */
export function ShopStoresHost() {
  const [open, setOpen] = useState(false);
  useEffect(() => { opener = () => setOpen(true); return () => { opener = null; }; }, []);
  return open ? <ShopStoresSheet onClose={() => setOpen(false)} /> : null;
}

/** Kişinin süzgeci (meta.ui.shop_hidden); yetenek yoksa null. */
export function useShopHidden(): ShopHidden | null {
  const on = useCap("shop_store_filter");
  const h = useMeta().data?.ui?.shop_hidden;
  return on ? h ?? { chains: [], local: false } : null;
}

function Switch({ on, label, onChange, disabled }: { on: boolean; label: string; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
      className={cx("relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-60",
        on ? "bg-green-600 dark:bg-green-500" : "bg-stone-300 dark:bg-stone-600")}>
      <span aria-hidden className={cx("inline-block size-5 rounded-full bg-white shadow transition-transform", on ? "translate-x-6" : "translate-x-1")} />
    </button>
  );
}

export function ShopStoresSheet({ onClose }: { onClose: () => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: STORES, retry: false, queryFn: () => send<StoresData>("/api/shopping-list/stores") });
  const [busy, setBusy] = useState(false);
  const d = q.data;

  const put = async (h: ShopHidden, body: { chains?: string[]; local?: boolean }) => {
    const prev = qc.getQueryData<StoresData>(STORES);
    if (prev) qc.setQueryData<StoresData>(STORES, applyHidden(prev, h));
    qc.setQueryData(["meta"], (m: { ui?: object } | undefined) => (m?.ui ? { ...m, ui: { ...m.ui, shop_hidden: h } } : m));
    setBusy(true);
    try {
      await send("/api/me/ui", { method: "PUT", json: { shop_hidden: body } });
    } catch (e) {
      if (prev) qc.setQueryData(STORES, prev);
      ui.error((e as Error).message);
    } finally {
      setBusy(false);
      for (const k of [...AFFECTED, ...STORES]) void qc.invalidateQueries({ queryKey: [k] });
    }
  };
  const setChain = (chain: string, visible: boolean) => {
    if (!d) return;
    const chains = toggleHidden(d.hidden.chains, chain, !visible);
    void put({ chains, local: d.hidden.local }, { chains });
  };
  const setLocal = (visible: boolean) => {
    if (!d) return;
    void put({ chains: d.hidden.chains, local: !visible }, { local: !visible });
  };

  return (
    <Modal title="Mağazalar" onClose={onClose}>
      <div className="space-y-4" data-testid="magazalar">
        <p className="text-sm text-stone-600 dark:text-stone-300">Girmek istemediğin mağazaları kapat; fiyatlarda ve planlarda hiç görünmez.</p>
        {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
        {q.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(q.error as Error).message}</p>}
        {d && (
          <>
            {!d.chains.length ? (
              <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700">Henüz fiyatı görülen mağaza yok.</p>
            ) : (
              <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700" aria-label="Zincir marketler">
                {d.chains.map((c) => (
                  <li key={c.chain} className="flex items-center gap-3 px-3 py-2.5" data-testid="magaza-satir">
                    <span className={cx("min-w-0 flex-1 break-words", c.hidden && "text-stone-400 dark:text-stone-500")}>{c.chain}</span>
                    <Switch on={!c.hidden} label={`${c.chain}: ${c.hidden ? "kapalı" : "açık"}`} onChange={(v) => setChain(c.chain, v)} disabled={busy} />
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center gap-3 rounded-lg border border-stone-200 px-3 py-2.5 dark:border-stone-700" data-testid="yerel-satir">
              <span className="min-w-0 flex-1">
                <span className={cx("block font-medium", d.local.hidden && "text-stone-400 dark:text-stone-500")}>Yerel marketler</span>
                <span className="block text-xs break-words text-stone-500 dark:text-stone-400">{localSub(d.local.names, d.local.count)}</span>
              </span>
              <Switch on={!d.local.hidden} label={`Yerel marketler: ${d.local.hidden ? "kapalı" : "açık"}`} onChange={setLocal} disabled={busy} />
            </div>
          </>
        )}
        <p className="text-xs text-stone-500 dark:text-stone-400">Bu ayar yalnız senin için; ortak defterdekiler kendi seçimini yapar.</p>
      </div>
    </Modal>
  );
}

/** "3 mağaza gizli · Değiştir" (yetenek shop_store_filter; count > 0). */
export function HiddenHint({ count, className }: { count?: number | null; className?: string }) {
  const on = useCap("shop_store_filter");
  const t = hiddenText(count);
  if (!on || !t) return null;
  return (
    <p className={cx("flex flex-wrap items-center gap-x-1 text-xs text-stone-500 dark:text-stone-400", className)} data-testid="gizli-ipucu">
      <EyeOff size={13} className="shrink-0" aria-hidden />
      <span>{t} ·</span>
      <button type="button" className="font-medium text-blue-700 underline underline-offset-2 hover:text-blue-800 dark:text-blue-300" onClick={openShopStores}>Değiştir</button>
    </p>
  );
}

/** Liste ekranındaki düğme: "Mağazalar" / "Mağazalar · 2 kapalı". */
export function StoresButton({ className }: { className?: string }) {
  const h = useShopHidden();
  if (!h) return null;
  return (
    <button type="button" className={cx("btn-ghost text-sm", className)} onClick={openShopStores} data-testid="magazalar-dugme">
      <Store size={16} aria-hidden /> {storesButtonText(h)}</button>
  );
}

/** Ayarlar satırı: "Mağazalar" — "Hepsi açık" / "2 kapalı". */
export function StoresSettingRow() {
  const h = useShopHidden();
  if (!h) return null;
  return (
    <section className="card p-4">
      <button type="button" onClick={openShopStores} data-testid="ayar-magazalar" className="flex w-full items-start gap-2 text-left text-sm">
        <Store size={18} className="mt-0.5 shrink-0 text-stone-500" aria-hidden />
        <span className="min-w-0 flex-1"><span className="font-semibold">Mağazalar</span>
          <span className="block text-xs text-stone-500">Girmek istemediğin mağazaları kapat; fiyatlarda ve planlarda hiç görünmez.</span></span>
        <span className="shrink-0 text-sm font-medium">{storesSettingValue(h)}</span>
        <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
      </button>
    </section>
  );
}
