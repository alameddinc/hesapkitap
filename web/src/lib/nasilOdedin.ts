// Zekât / sadaka "Nasıl ödedin?" (sunucu yetenekleri donation_payment, donation_payment_gold): seçenekler, ipuçları,
// sunucuya giden gövde ve kayıtlı ödemenin düzeltilmesi. Her ödeme gider olarak da yazılır (Bağış & Sadaka). Hesap/kart
// seçimi isteğe bağlı. Altınla (bozdurup ya da doğrudan) verilen hesaptan düşmez; zekâtta altın varlığından düşülebilir.
// Mobil components/zekat/NasilVerdin.tsx ile aynı kural, aynı cümleler.

export type NasilKey = "banka" | "kredi_karti" | "nakit" | "havale" | "altin_bozdurma" | "altin";

export interface NasilState {
  how: NasilKey; account: number | null; card: string; recipient: string;
  /** altınla: düşülecek altın varlığı (ops.) ve miktarı (gram / adet) */
  asset: number | null; qty: number | null;
}

/** varsayılan banka hesabından: zekât/sadaka ödemesinde borçla (kartla) ödeme seçili gelmesin */
export const NASIL_START: NasilState = { how: "banka", account: null, card: "", recipient: "", asset: null, qty: null };

export const ALTIN: readonly NasilKey[] = ["altin_bozdurma", "altin"];
export const isAltin = (k: NasilKey | null | undefined) => !!k && ALTIN.includes(k);

/** kısa ad (kayıtlı ödeme satırında) */
export const NASIL_AD: Record<NasilKey, string> = {
  banka: "Banka hesabından", kredi_karti: "Kredi kartı", nakit: "Elden (nakit)", havale: "Havale / EFT",
  altin_bozdurma: "Altın bozdurup", altin: "Altın vererek",
};

/** seçenekler: eski sunucuda (altın yeteneği yok) altın seçenekleri görünmez */
export const nasilSecenek = (altin: boolean): NasilKey[] =>
  altin ? ["banka", "kredi_karti", "nakit", "havale", "altin_bozdurma", "altin"] : ["banka", "kredi_karti", "nakit", "havale"];

export const NASIL_IPUCU: Record<NasilKey, string> = {
  banka: "Hesap seçersen o hesabın bakiyesinden düşer; döküm gelince aynı hareketle birleşir. Hesap seçmeden de kaydedebilirsin.",
  kredi_karti: "Hemen gider olarak görünür (ekstre bekleniyor). Ekstre gelince o satırla eşleşir, iki kez sayılmaz.",
  nakit: "Elden verdin: cüzdan seçersen ondan düşer. Seçmeden de kaydedebilirsin.",
  havale: "Hesabın bakiyesinden düşer. Döküm gelince aynı havaleyle birleşir, iki kez sayılmaz.",
  altin_bozdurma: "Altını bozdurup parasını verdin: TL karşılığı gider olarak yazılır, hesap bakiyesinden düşmez.",
  altin: "Doğrudan altın verdin: TL karşılığı gider olarak yazılır, hesaptan para çıkmaz.",
};

export type NasilGovde = {
  payment?: "banka" | "nakit" | "havale" | "kredi_karti" | "altin_bozdurma" | "altin";
  account_id?: number | null; card_last4?: string | null; recipient?: string; asset_id?: number | null; asset_qty?: number | null;
};

/** POST gövdesine eklenecek alanlar. Eski sunucu (altin=false) "banka"yı bilmez: banka kartı = "nakit" + banka hesabı. */
export function nasilGovde(s: NasilState, altin: boolean): NasilGovde {
  const card = s.how === "kredi_karti", gold = isAltin(s.how);
  const payment = !altin && s.how === "banka" ? "nakit" : s.how;
  return {
    payment, account_id: card || gold ? null : s.account,
    card_last4: card && s.card ? s.card : null,
    ...(s.how === "havale" && s.recipient.trim() ? { recipient: s.recipient.trim() } : {}),
    ...(altin && gold && s.asset && s.qty && s.qty > 0 ? { asset_id: s.asset, asset_qty: s.qty } : {}),
  };
}

/** sunucunun döndürdüğü kayıtlı ödeme (GET /api/zakat people[].payments[]; how vb. altın yeteneğiyle gelir) */
export interface KayitliOdeme {
  id: number; amount: number; pay_date: string; note: string | null;
  how?: NasilKey | null; account_id?: number | null; card_last4?: string | null; method_editable?: boolean;
  asset_id?: number | null; asset_qty?: number | null; asset_label?: string | null; asset_unit?: string | null;
}

/** düzenlemeye açılırken kayıtlı ödemenin şekli (şekli girilmemişse varsayılan) */
export const nasilDurumu = (p: KayitliOdeme): NasilState => p.how ? {
  how: p.how, account: p.account_id ?? null, card: p.card_last4 ?? "", recipient: "",
  asset: p.asset_id ?? null, qty: p.asset_qty ?? null,
} : NASIL_START;

/** PATCH /api/zakat/payments/{id}: yalnız değişen alanlar. Ödeme şekli yalnız altın yeteneği varken ve şekil
 *  değişebiliyorsa (method_editable) gönderilir; şekli girilmemiş eski ödemede seçilen şekil gider açar. */
export function odemeDuzeltGovde(p: KayitliOdeme, yeni: { amount: number; pay_date: string; nasil: NasilState }, altin: boolean) {
  const b: { amount?: number; pay_date?: string } & NasilGovde = {};
  if (yeni.amount !== p.amount) b.amount = yeni.amount;
  if (yeni.pay_date !== p.pay_date.slice(0, 10)) b.pay_date = yeni.pay_date;
  if (altin && p.method_editable !== false) {
    const once = nasilGovde(nasilDurumu(p), true), simdi = nasilGovde(yeni.nasil, true);
    if (!p.how || JSON.stringify(once) !== JSON.stringify(simdi)) Object.assign(b, simdi);
  }
  return b;
}

/** kayıtlı ödeme satırının alt yazısı: "Altın vererek · Çeyrek altın 2 adet" */
export function odemeAltYazi(p: KayitliOdeme): string {
  const parts = [p.how ? NASIL_AD[p.how] : null,
    p.asset_qty && p.asset_label ? `${p.asset_label} ${p.asset_qty.toLocaleString("tr-TR")} ${p.asset_unit ?? ""}`.trim() : null,
    p.note];
  return parts.filter(Boolean).join(" · ");
}

/** seçilen altından elindekinden fazlası düşülmek isteniyor mu (Kaydet kapalı; sunucu da 422 verir) */
export const altinAsimi = (s: NasilState, golds?: AltinVarlik[]) =>
  isAltin(s.how) && !!s.asset && (s.qty ?? 0) > (golds?.find((g) => g.id === s.asset)?.qty ?? Infinity);

/** altın varlıkları (kişinin "varlik" satırlarından): seçicide "Çeyrek altın · 4 adet" */
export interface AltinVarlik { id: number; label: string; qty: number; unit: string }
const ALTIN_TURU = ["ALTIN_24", "ALTIN_22", "ALTIN_18", "ALTIN_14", "CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA"];
export const altinVarliklari = (lines: { id?: number; kind?: string; source: string; label: string; qty?: number; unit?: string }[]): AltinVarlik[] =>
  lines.filter((l) => l.source === "varlik" && l.id && l.kind && ALTIN_TURU.includes(l.kind) && (l.qty ?? 0) > 0)
    .map((l) => ({ id: l.id!, label: l.label, qty: l.qty!, unit: l.unit ?? "" }));
