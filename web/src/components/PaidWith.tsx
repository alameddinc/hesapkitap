import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { parseAmount } from "../format";
import { NASIL_AD, NASIL_IPUCU, NASIL_START, isAltin, nasilGovde, nasilSecenek, type AltinVarlik, type NasilKey,
  type NasilState } from "../lib/nasilOdedin";
import AccountSelect from "./AccountSelect";
import { useMeta } from "./hooks";
import SorguDurumu from "./SorguDurumu";

/** Zekât / sadaka "Nasıl ödedin?" (sunucu yetenekleri donation_payment, donation_payment_gold): her ödeme gider olarak da
 *  yazılır (Bağış & Sadaka). Hesap/kart seçimi isteğe bağlı. Kredi kartı: hemen "ekstre bekleniyor" gider, ekstre gelince
 *  o satırla eşleşir (tek sayım). Banka hesabından ve havale: seçilen hesabın bakiyesinden düşer, döküm gelince aynı
 *  hareketle birleşir. Elden: cüzdandan. Altın bozdurup / altın vererek: TL karşılığı gider, hesaptan düşmez; zekâtta
 *  altın varlığından düşülebilir. Kural ve cümleler lib/nasilOdedin (mobil components/zekat/NasilVerdin ile aynı). */
export type PaidWithValue = NasilKey;
export type PaidWithState = NasilState;
export const PAID_WITH_START: PaidWithState = NASIL_START;

/** sunucu altın seçeneklerini ve ödeme şeklini düzeltmeyi biliyor mu (eski sunucuda seçenekler eskisi gibi) */
export const usePaidWithGold = () => !!useMeta().data?.capabilities?.includes("donation_payment_gold");

export const paidWithBody = (s: PaidWithState, gold: boolean) => nasilGovde(s, gold);

export default function PaidWith({ id, value, onChange, noRecipient, golds }: {
  id: string; value: PaidWithState; onChange: (v: PaidWithState) => void;
  /** formda zaten "Kime" alanı var (sadaka) */ noRecipient?: boolean;
  /** altınla ödemede düşülebilecek altın varlıkları (zekâtta kişinin varlıkları; yoksa seçici görünmez) */ golds?: AltinVarlik[];
}) {
  const gold = usePaidWithGold();
  const statements = useQuery({ queryKey: ["statements"], queryFn: api.statements, enabled: value.how === "kredi_karti" });
  const cards = [...new Map((statements.data ?? []).map((s) => [s.card_no.slice(-4), `${s.bank} ··${s.card_no.slice(-4)}`])).entries()];
  const options = nasilSecenek(gold);
  const altin = isAltin(value.how);
  const picked = golds?.find((g) => g.id === value.asset);
  return (
    <div className="space-y-2">
      <div>
        <label className="label" htmlFor={id}>Nasıl ödedin?</label>
        <select id={id} className="input" value={value.how}
          onChange={(e) => onChange({ ...value, how: e.target.value as PaidWithValue, account: null, asset: null, qty: null })}>
          {options.map((k) => <option key={k} value={k}>{NASIL_AD[k]}</option>)}
          {!options.includes(value.how) && <option value={value.how}>{NASIL_AD[value.how]}</option>}
        </select>
        <p className="mt-1 text-xs text-stone-500">{NASIL_IPUCU[value.how]}</p>
      </div>
      {value.how === "kredi_karti" && <SorguDurumu q={statements} kompakt etiket="Kartlar yükleniyor" />}
      {value.how === "kredi_karti" && cards.length > 1 && (
        <select className="input" aria-label="Hangi kart" value={value.card} onChange={(e) => onChange({ ...value, card: e.target.value })}>
          <option value="">Hangi kart? (ops.)</option>
          {cards.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      )}
      {value.how !== "kredi_karti" && !altin && (
        <AccountSelect id={`${id}-acc`} label={value.how === "nakit" ? "Hangi cüzdan (ops.)" : "Hangi hesaptan (ops.)"}
          kinds={value.how === "nakit" ? ["nakit"] : ["banka"]} noneLabel="Belirtme"
          value={value.account} onChange={(a) => onChange({ ...value, account: a })} />
      )}
      {altin && gold && !!golds?.length && (
        <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-[minmax(0,1fr)_8rem]">
          <div className="min-w-0">
            <label className="label" htmlFor={`${id}-gold`}>Hangi altından düşülsün (ops.)</label>
            <select id={`${id}-gold`} className="input" value={value.asset ?? ""}
              onChange={(e) => onChange({ ...value, asset: e.target.value ? Number(e.target.value) : null, qty: null })}>
              <option value="">Düşme</option>
              {golds.map((g) => <option key={g.id} value={g.id}>{g.label} · {g.qty.toLocaleString("tr-TR")} {g.unit}</option>)}
            </select>
          </div>
          {picked && (
            <div>
              <label className="label" htmlFor={`${id}-qty`}>Kaç {picked.unit}</label>
              <input id={`${id}-qty`} className="input text-right" inputMode="decimal" placeholder={`en çok ${picked.qty.toLocaleString("tr-TR")}`}
                defaultValue={value.qty != null ? String(value.qty).replace(".", ",") : ""}
                onChange={(e) => { const n = parseAmount(e.target.value); onChange({ ...value, qty: n > 0 ? n : null }); }} />
            </div>
          )}
          {picked && (value.qty ?? 0) > picked.qty
            ? <p className="text-xs text-red-600 min-[420px]:col-span-2 dark:text-red-400">En çok {picked.qty.toLocaleString("tr-TR")} {picked.unit} düşülebilir.</p>
            : picked && <p className="text-xs text-stone-500 min-[420px]:col-span-2">Varlıklarından bu kadar düşülür; ödemeyi silersen geri eklenir.</p>}
        </div>
      )}
      {value.how === "havale" && !noRecipient && (
        <div>
          <label className="label" htmlFor={`${id}-to`}>Kime (ops.)</label>
          <input id={`${id}-to`} className="input" maxLength={80} placeholder="kişi ya da kurum" value={value.recipient}
            onChange={(e) => onChange({ ...value, recipient: e.target.value })} />
        </div>
      )}
    </div>
  );
}
