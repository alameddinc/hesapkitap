import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import AccountSelect from "./AccountSelect";
import SorguDurumu from "./SorguDurumu";

/** Zekât / sadaka "Nasıl ödedin?" (sunucu yeteneği donation_payment): her ödeme gider olarak da yazılır (Bağış & Sadaka).
 *  Kredi kartı: hemen "ekstre bekleniyor" gider, ekstre gelince o satırla eşleşir (tek sayım). Banka hesabından (banka
 *  kartı) ve havale: o hesabın bakiyesinden düşer, döküm gelince aynı hareketle birleşir. Nakit: cüzdandan. Mobil
 *  components/zekat/NasilVerdin ile aynı. */
export type PaidWithValue = "kredi_karti" | "banka" | "nakit" | "havale";

export interface PaidWithState { how: PaidWithValue; account: number | null; card: string; recipient: string }

export const PAID_WITH_START: PaidWithState = { how: "kredi_karti", account: null, card: "", recipient: "" };

export const paidWithBody = (s: PaidWithState) => ({
  payment: s.how === "banka" ? "nakit" : s.how,  // banka kartı = sunucuda "nakit / banka kartı" + banka hesabı
  account_id: s.how === "kredi_karti" ? null : s.account,
  card_last4: s.how === "kredi_karti" && s.card ? s.card : null,
  ...(s.how === "havale" && s.recipient.trim() ? { recipient: s.recipient.trim() } : {}),
});

const HINT: Record<PaidWithValue, string> = {
  kredi_karti: "Hemen gider olarak görünür (ekstre bekleniyor). Ekstre gelince o satırla eşleşir, iki kez sayılmaz.",
  banka: "Hesabın bakiyesinden düşer. Döküm gelince aynı hareketle birleşir, iki kez sayılmaz.",
  nakit: "Cüzdandan (nakit) düşer.",
  havale: "Hesabın bakiyesinden düşer. Döküm gelince aynı havaleyle birleşir, iki kez sayılmaz.",
};

export default function PaidWith({ id, value, onChange, noRecipient }: {
  id: string; value: PaidWithState; onChange: (v: PaidWithState) => void;
  /** formda zaten "Kime" alanı var (sadaka) */ noRecipient?: boolean;
}) {
  const statements = useQuery({ queryKey: ["statements"], queryFn: api.statements, enabled: value.how === "kredi_karti" });
  const cards = [...new Map((statements.data ?? []).map((s) => [s.card_no.slice(-4), `${s.bank} ··${s.card_no.slice(-4)}`])).entries()];
  return (
    <div className="space-y-2">
      <div>
        <label className="label" htmlFor={id}>Nasıl ödedin?</label>
        <select id={id} className="input" value={value.how}
          onChange={(e) => onChange({ ...value, how: e.target.value as PaidWithValue, account: null })}>
          <option value="kredi_karti">Kredi kartı</option>
          <option value="banka">Banka hesabından (banka kartı)</option>
          <option value="nakit">Nakit</option>
          <option value="havale">Havale / EFT</option>
        </select>
        <p className="mt-1 text-xs text-stone-500">{HINT[value.how]}</p>
      </div>
      {value.how === "kredi_karti" && <SorguDurumu q={statements} kompakt etiket="Kartlar yükleniyor" />}
      {value.how === "kredi_karti" && cards.length > 1 && (
        <select className="input" aria-label="Hangi kart" value={value.card} onChange={(e) => onChange({ ...value, card: e.target.value })}>
          <option value="">Hangi kart?</option>
          {cards.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      )}
      {value.how !== "kredi_karti" && (
        <AccountSelect id={`${id}-acc`} label={value.how === "nakit" ? "Hangi cüzdan (ops.)" : "Hangi hesaptan"}
          kinds={value.how === "nakit" ? ["nakit"] : ["banka"]} noneLabel="Belirtme"
          value={value.account} onChange={(a) => onChange({ ...value, account: a })} />
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
