// Özet "Yapılacaklar": bekleyen belgeler (kart ekstresi, hesap dökümü) iki ve daha çoksa tek grupta toplanır — "3 belge
// bekleniyor: Örnek Bank ••1234 (ekstre 14 Kas), Mavi Bank ••5678 (ekstre 3 Kas), Örnek Bank Vadesiz (döküm)" — her
// belge yine kendi satırı, Yükle'si ve Yarın/Bu ay gösterme seçeneğiyle. creditcardekstre
// web/src/lib/belgeGrubu.ts ile hesapkitap-mobile src/lib/belgeGrubu.ts aynı dosyadır (biri değişirse öteki de).
// short: sunucu (yetenek reminders_doc_group); eski sunucuda başlıktan kurulur.

export interface BelgeMaddesi { id: string; kind: string; title: string; short?: string; bank?: string; last4?: string }

export type Birim<T> = { tip: "tek"; r: T } | { tip: "grup"; items: T[] };

/** Yüklenmesi beklenen belge mi (kesilen ekstre, dönemi kapanan döküm)? */
export const beklenenBelge = (r: { kind: string }) => r.kind === "ekstre" || r.kind === "dokum";

/** Liste sırası korunur; grup, ilk belgenin yerinde durur. Tek belge grup olmaz. */
export function grupla<T extends BelgeMaddesi>(items: T[]): Birim<T>[] {
  const docs = items.filter(beklenenBelge);
  if (docs.length < 2) return items.map((r) => ({ tip: "tek", r }));
  const out: Birim<T>[] = [];
  for (const r of items) {
    if (!beklenenBelge(r)) out.push({ tip: "tek", r });
    else if (r === docs[0]) out.push({ tip: "grup", items: docs });
  }
  return out;
}

export const grupBaslik = (n: number) => `${n} belge bekleniyor`;

export function kisaAd(r: BelgeMaddesi): string {
  if (r.short) return r.short;
  const bank = r.bank ?? r.title.split(" · ")[0] ?? r.title;
  return r.kind === "dokum" ? `${bank} (döküm)` : r.last4 ? `${bank} ••${r.last4} (ekstre)` : `${bank} (ekstre)`;
}

export const grupOzet = (items: BelgeMaddesi[]) => items.map(kisaAd).join(", ");
