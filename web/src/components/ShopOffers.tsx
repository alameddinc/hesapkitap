// Marka + boy (yetenek shop_brand_size): Marka çipleri ("Marka fark etmez" + markalar) ve "Mağazalara göre" listesi
// (GET /api/prices/offers?group=<boylu anahtar>&brand=<marka>): bu boy (ve marka) hangi mağazada kaça, ucuzdan pahalıya.
// Ürünü düzenle penceresinde ve Fiyatlar grup ayrıntısında aynı bileşen ve metin. Satırlarda yalnız zincir adı;
// resmî kaynak dipnotu mevcut kurallarla (PriceFootnote). Mobil karşılığı: hesapkitap-mobile.
import { useQuery } from "@tanstack/react-query";
import { maskText, todayISO } from "../format";
import { cx } from "../format";
import { send } from "../lib/apiError";
import { chainName } from "../lib/shopList";
import { offerSub, offersTitle, type OffersData } from "../lib/shopStores";
import { OfferBadges, PriceFootnote, type GroupBest } from "./PriceDetail";
import { HiddenHint } from "./ShopStores";
import { PriceStatus } from "./ShopCombos";

export interface BrandOpt { brand: string; sizes: string[]; chains: number; /** yeni sunucu: {anahtar: "1 L"} */ size_labels?: Record<string, string> }

/** "Marka fark etmez" + markalar; null = marka fark etmez. */
export function BrandChips({ brands, value, onChange, id = "marka" }: {
  brands: BrandOpt[]; value: string | null; onChange: (b: string | null) => void; id?: string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-labelledby={`${id}-l`} data-testid="marka-cipleri">
      {[null, ...brands.map((b) => b.brand)].map((b) => {
        const on = (value ?? null) === b;
        return (
          <button key={b ?? "_"} type="button" role="radio" aria-checked={on} onClick={() => onChange(b)}
            className={cx("hit inline-flex min-h-9 max-w-full items-center rounded-full border px-3 py-1 text-left text-sm break-words",
              on ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-600"
                : "border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:text-stone-200 dark:hover:bg-stone-800")}>
            {b ?? "Marka fark etmez"}</button>
        );
      })}
    </div>
  );
}

export const offersKey = (group: string, brand: string | null) => ["shop-offers", group, brand ?? ""] as const;
export const fetchOffers = (group: string, brand: string | null) =>
  send<OffersData>(`/api/prices/offers?group=${encodeURIComponent(group)}${brand ? `&brand=${encodeURIComponent(brand)}` : ""}`);

/** Teklifleri getirir (Mağaza seçimi de aynı sorguyu kullanır: önbellekten). */
export const useOffers = (group: string | null, brand: string | null, enabled = true) => useQuery({
  queryKey: offersKey(group ?? "", brand), enabled: enabled && !!group, retry: false, staleTime: 5 * 60_000,
  queryFn: () => fetchOffers(group!, brand),
});

/** "Mağazalara göre": başlık "Sütaş · 1 L"; satır zincir — fiyat, altında birim fiyat ve kaynak; en ucuza rozet. */
export function OffersList({ group, brand, sizeLabel, money }: { group: string; brand: string | null; sizeLabel?: string | null; money: (n: number) => string }) {
  const q = useOffers(group, brand);
  const d = q.data;
  const today = todayISO();
  const offers = d?.offers ?? [];
  // dipnot kuralı değişmez: resmî satırlara sunucunun kaynak yazısı
  const foot: GroupBest[] = offers.map((o) => ({ ...o, source: o.source as GroupBest["source"], per_unit: (o.per_unit ?? null) as GroupBest["per_unit"],
    attribution: o.source === "resmi" ? d?.attribution?.[0] ?? null : null }));
  return (
    <section className="space-y-2" data-testid="magazalara-gore" aria-label="Mağazalara göre">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <h3 className="label mb-0">Mağazalara göre</h3>
        <span className="text-sm font-medium break-words text-stone-700 dark:text-stone-200" data-testid="magazalara-baslik">{offersTitle(d?.brand ?? brand, d?.size_label ?? sizeLabel)}</span>
      </div>
      <PriceStatus q={q} rows={3} testId="mg-yukleniyor" />
      {d && !offers.length && <p className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-500 dark:border-stone-700">Bu marka ve boy için fiyat yok.</p>}
      {offers.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
          {offers.map((o, i) => {
            const cheap = i === 0 && offers.length > 1;
            const sub = offerSub(o, today, money);
            return (
              <li key={`${o.chain}-${i}`} className={cx("flex items-start gap-3 px-3 py-2", cheap && "bg-green-50/60 dark:bg-green-950/30")} data-testid="magaza-teklif">
                <span className="min-w-0 flex-1">
                  <span className={cx("flex flex-wrap items-center gap-1.5 font-medium break-words", cheap && "text-green-800 dark:text-green-300")}>
                    <span className="min-w-0 break-words">{maskText(chainName(o.chain))}</span>
                    {cheap && <span className="rounded bg-green-100 px-1.5 py-0.5 text-[11px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300">En ucuz</span>}
                    <OfferBadges b={o as GroupBest} />
                  </span>
                  {sub && <span className="block text-xs break-words text-stone-500 tabular-nums dark:text-stone-400">{maskText(sub)}</span>}
                </span>
                <span className={cx("shrink-0 text-right font-medium tabular-nums", cheap && "text-green-800 dark:text-green-300")}>{money(o.price)}</span>
              </li>
            );
          })}
        </ul>
      )}
      <HiddenHint count={d?.hidden?.count} />
      <PriceFootnote best={foot} today={today} />
    </section>
  );
}
