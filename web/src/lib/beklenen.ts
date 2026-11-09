// Özet → "Dönem sonu" kartı ve Planlama ayının dökümü (sunucu yetenekleri "summary_expected" + "summary_forecast":
// /api/summary expected, /api/plan months[].parts). Metinler web ve mobilde BİREBİR aynı: creditcardekstre
// web/src/lib/beklenen.ts ile hesapkitap-mobile src/lib/beklenen.ts aynı dosyadır (biri değişirse öteki de). Rakamlar
// sunucudan; burada toplama yok.
//
// Anlam (sunucu finance.expected = Planlama'nın o ayki satırı):
//   net          = "Yeni harcama yapmazsan": gerçekleşen gelir/gider + bekleyenler (ekstresi gelmemiş taksit ve abonelik,
//                  girilmemiş sabit kalem, ödenmemiş fatura, bekleyen gelir)
//   forecast_net = "Her zamanki harcamanla": net − son dönemlere göre tahmini kart harcaması (variable_estimate);
//                  Planlama'nın "kenara kalacak" rakamı
// Geçmiş dönemde expected null → kart yok. Eski sunucu (forecast_net yok) → kart yok.

export interface BeklenenParcalar {
  recorded_expense: number;
  pending_installments: number;
  pending_subscriptions: number;
  pending_recurring_expense: number;
  pending_bills: number;
  pending_expense: number;
  recorded_income: number;
  pending_income: number;
}

export interface Beklenen {
  month: string;
  source: "plan";
  expense: number;
  income: number;
  net: number;
  /** son dönemlere göre tahmini kart harcaması (Planlama months[].estimated); yetenek summary_forecast */
  variable_estimate?: number;
  /** her zamanki harcamanla dönem sonu (Planlama months[].net_with_estimate); yetenek summary_forecast */
  forecast_net?: number;
  parts: BeklenenParcalar;
}

/** Dönem sonu kartı ve dökümün kaynağı: Özet'te summary.expected, Planlama'da o ayın satırı (donemSonuPlandan). */
export interface DonemSonu {
  income: number;
  expense: number;
  net: number;
  variable_estimate: number;
  forecast_net: number;
  parts: BeklenenParcalar;
}

const KURUS = 0.005;
/** işaret ile tutar arasında görünmez "kelime birleştirici": "+" / "₺77.921" ayrı satıra düşmesin */
const WJ = "⁠";

/** Tam lira, kuruşsuz: "₺137.166" (eksi "−₺", arada WJ). Gizleme çağıranın para işlevinde. */
export const tamTl = (n: number) =>
  (n < 0 ? `−${WJ}₺` : "₺") + new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(Math.abs(Math.round(n)));

/** "+₺25.000" / "−₺1.200" / "₺0" (işaret ile tutar arasında WJ). para işaretsiz tutarı biçimler (gizliyse ₺•••). */
export const isaretli = (n: number, para: (n: number) => string) =>
  `${n > KURUS ? `+${WJ}` : n < -KURUS ? `−${WJ}` : ""}${para(Math.abs(n))}`;

/** Yalnız işaret ("+" / "−" / ""): tutar ayrı biçimlenip bölünmeyen kutuda yanına yazılır (Planlama dökümü). */
export const isaret = (n: number) => (n > KURUS ? "+" : n < -KURUS ? "−" : "");

/** Özet'teki kart görünsün mü: içinde bulunulan/ileri dönem (expected var) ve sunucu tahmini veriyor. */
export const donemSonuVar = (e: Beklenen | null | undefined): e is Beklenen & DonemSonu =>
  !!e && typeof e.forecast_net === "number" && typeof e.variable_estimate === "number";

/** Planlama ayından (/api/plan months[]): eski sunucuda parts yok → null. */
export function donemSonuPlandan(m: {
  income: number; expense: number; net: number; estimated: number; net_with_estimate: number; parts?: BeklenenParcalar;
}): DonemSonu | null {
  if (!m.parts) return null;
  return { income: m.income, expense: m.expense, net: m.net, variable_estimate: m.estimated, forecast_net: m.net_with_estimate, parts: m.parts };
}

/** eksiyse kırmızı */
export const eksiMi = (n: number) => n < -KURUS;

/** Kartın ve dökümün metinleri (iki istemcide aynı). */
export const DONEM_SONU = {
  baslik: "Dönem sonu",
  her: "Her zamanki harcamanla",
  yapmazsan: "Yeni harcama yapmazsan",
  planlama: "Planlamada gör",
  // Harcanabilir (bugün) eksi, Dönem sonu artıyken: ikisi neden ters (denetim: rakamlar birbirini tutmuyor)
  iliski: "Harcanabilir bugünün rakamı; beklenen gelirler (maaş gibi) gelince dönem artıda kapanır.",
} as const;

/** Kartın ikinci satırı: "Yeni harcama yapmazsan +₺70.804". */
export const donemSonuAlt = (d: DonemSonu, para: (n: number) => string) => `${DONEM_SONU.yapmazsan} ${isaretli(d.net, para)}`;

export interface DokumSatiri {
  /** satırın anahtarı (test kimliği) */
  key: string;
  label: string;
  /** işaretli tutar: gelir +, gider − */
  amount: number;
  /** 1: üstteki Gelir / Kesin gider satırının kırılımı (girintili, soluk) */
  level: 0 | 1;
  /** ara toplam (yeni harcama yapmazsan) / sonuç (her zamanki harcamanla) */
  total?: "ara" | "son";
}

/** Planlama ayının dökümü: Gelir (kayıtlı + bekleniyor), Kesin gider (kayıtlı + ekstresi gelmemiş taksit/abonelik,
 *  girilmemiş sabit gider, ödenmemiş fatura) → Yeni harcama yapmazsan → tahmini kart harcaması → Her zamanki
 *  harcamanla. Kırılım yalnız bekleyen varsa (yoksa üst satırla aynı olurdu); sıfır olan bekleyen satır yok. */
export function donemSonuDokum(d: DonemSonu): DokumSatiri[] {
  const p = d.parts;
  const out: DokumSatiri[] = [];
  const kir = (rows: [keyof BeklenenParcalar, string][], sign: 1 | -1) => {
    for (const [key, label] of rows) if (Math.abs(p[key]) > KURUS) out.push({ key, label, amount: sign * p[key], level: 1 });
  };
  out.push({ key: "income", label: "Gelir", amount: d.income, level: 0 });
  if (p.pending_income > KURUS) kir([["recorded_income", "Kayıtlı"], ["pending_income", "Bekleniyor"]], 1);
  out.push({ key: "expense", label: "Kesin gider", amount: -d.expense, level: 0 });
  if (p.pending_expense > KURUS) {
    kir([["recorded_expense", "Kayıtlı"], ["pending_installments", "Ekstresi gelmemiş taksitler"],
      ["pending_subscriptions", "Ekstresi gelmemiş abonelikler"], ["pending_recurring_expense", "Girilmemiş sabit giderler"],
      ["pending_bills", "Ödenmemiş faturalar"]], -1);
  }
  out.push({ key: "net", label: DONEM_SONU.yapmazsan, amount: d.net, level: 0, total: "ara" });
  if (d.variable_estimate > KURUS) {
    out.push({ key: "variable_estimate", label: "Tahmini kart harcaması", amount: -d.variable_estimate, level: 0 });
  }
  out.push({ key: "forecast_net", label: DONEM_SONU.her, amount: d.forecast_net, level: 0, total: "son" });
  return out;
}
