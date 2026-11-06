import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, Users } from "lucide-react";
import { useState } from "react";
import type { Row } from "../api";
import { call } from "../lib/apiError";
import { cx, yonelme } from "../format";
import { useUI } from "../ui";
import { type BookPerson, isViewer, useBook, usePeople, useSave } from "./hooks";
import Modal from "./Modal";
import { useLock } from "./Plan";

// Aile: "Kim harcadı" atama (sunucu yeteneği "spender_assign", paket satırı "aile_rapor"; mobil defter/Harcayan.tsx ile
// aynı davranış ve metinler).
//   Hesaplar → kredi kartı → "Kimin kartı": kartın ekstre işlemleri o kişiye yazılır (Ortak: kimseye).
//     PUT /api/cards/{card_no}/spender {who: u<id> | null}; GET /api/cards/spenders → {cards: {card_no: "u7"}}
//   İşlem penceresi → "Kim harcadı" çipleri: tek kaydın harcayanı (elle kayıt, kart işlemi, sabit kalem — sabit kalemde
//     her ay); "Otomatik" sunucunun kuralına döner. PUT /api/spender {source, id, who: u<id> | ortak | null} → {who, set}
// Hangi kişinin harcadığını sunucu bulur; burada yalnız seçim yollanır. Yalnız ortak defterde (Benim'de değil);
// sadece görür değeri görür, değiştiremez.

export const ORTAK = "ortak";
const TONES = [
  "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
];
/** Çubuk rengi (Kim ne harcadı, ortak bütçe): kişinin noktasıyla aynı sıra. */
export const BARS = ["bg-blue-600", "bg-green-600", "bg-amber-500", "bg-violet-600"];
export const ORTAK_BAR = "bg-stone-400 dark:bg-stone-500";

const personOf = (people: BookPerson[], key: string) => (key === ORTAK ? undefined : people.find((p) => `u${p.id}` === key));
export const barOf = (people: BookPerson[], key: string) => {
  const p = personOf(people, key);
  return p ? BARS[people.indexOf(p) % BARS.length] : ORTAK_BAR;
};

/** Kişinin baş harfi ya da Ortak için kişiler simgesi. */
export function KisiNokta({ k, people, size = 24 }: { k: string; people: BookPerson[]; size?: number }) {
  const p = personOf(people, k);
  return (
    <span aria-hidden="true" style={{ width: size, height: size, fontSize: Math.round(size * 0.5) }}
      className={cx("inline-flex shrink-0 items-center justify-center rounded-full font-semibold leading-none",
        p ? TONES[people.indexOf(p) % TONES.length] : "bg-stone-200 text-stone-600 dark:bg-stone-700 dark:text-stone-300")}>
      {p ? p.name.charAt(0).toLocaleUpperCase("tr") : <Users size={Math.round(size * 0.58)} />}
    </span>
  );
}

/** Atama açık mı (ortak defter, paket izinli) ve değiştirilebilir mi (sadece görür değil); kişiler ve adlar. */
export function useSpenders() {
  const book = useBook().data;
  const pp = usePeople(); // "Benim" defterinde boş
  const lock = useLock("aile_rapor");
  const on = pp.multi && !lock;
  const adOf = (k: string | null | undefined) => {
    if (!k || k === ORTAK) return "Ortak";
    const p = personOf(pp.people, k);
    return p ? (pp.isMe(p.id) ? "Sen" : p.name) : "Defterden ayrılan kişi";
  };
  /** "Ayşe'ye" / "sana" / "Ortak'a" */
  const adaOf = (k: string | null | undefined) => (adOf(k) === "Sen" ? "sana" : yonelme(adOf(k)));
  const keys = [ORTAK, ...pp.people.map((p) => `u${p.id}`)];
  return { on, edit: on && !isViewer(book), people: pp.people, keys, adOf, adaOf, isMe: pp.isMe };
}

const useCardSpenders = (on: boolean) => useQuery({
  queryKey: ["card-spenders"], enabled: on, retry: false, staleTime: 60_000,
  queryFn: () => call<{ cards: Record<string, string> }>("GET", "/api/cards/spenders"),
});

// ---------------------------------------------------------------- Hesaplar: "Kimin kartı"

/** Kredi kartı satırının altında: "Kimin kartı: Ayşe" ve Değiştir (sadece görürde yalnız değer). */
export function CardSpender({ cardNo }: { cardNo: string }) {
  const s = useSpenders();
  const q = useCardSpenders(s.on);
  const [open, setOpen] = useState(false);
  if (!s.on || !q.data) return null;
  const who = q.data.cards?.[cardNo] ?? ORTAK;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-stone-500" data-testid="kart-harcayan">
      <span>Kimin kartı:</span>
      <KisiNokta k={who} people={s.people} size={16} />
      <b className="font-medium text-stone-700 [overflow-wrap:anywhere] dark:text-stone-200">{s.adOf(who)}</b>
      <span>· ekstre işlemleri {who === ORTAK ? "kimseye yazılmaz" : `${s.adaOf(who)} yazılır`}</span>
      {s.edit && <button className="font-medium text-blue-700 hover:underline dark:text-blue-400" aria-haspopup="dialog"
        aria-label={`Kimin kartı: ${s.adOf(who)}. Değiştir`} onClick={() => setOpen(true)}>Değiştir</button>}
      {open && <CardSpenderForm cardNo={cardNo} who={who} onClose={() => setOpen(false)} />}
    </span>
  );
}

function CardSpenderForm({ cardNo, who, onClose }: { cardNo: string; who: string; onClose: () => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const s = useSpenders();
  const save = useSave((w: string) => call<{ card_no: string; who: string | null }>("PUT",
    `/api/cards/${encodeURIComponent(cardNo)}/spender`, { who: w === ORTAK ? null : w }), (r) => {
    void qc.invalidateQueries({ queryKey: ["card-spenders"] });
    ui.toast(`Kartın işlemleri ${s.adaOf(r.who ?? ORTAK)} yazılır`);
    onClose();
  });
  const pick = (w: string) => (w === who ? onClose() : save.mutate(w));
  return (
    <Modal title="Kimin kartı" onClose={onClose}>
      <div className="space-y-3">
        <div className="divide-y divide-stone-100 rounded-xl border border-stone-200 dark:divide-stone-800 dark:border-stone-800" role="radiogroup" aria-label="Kimin kartı">
          {s.keys.map((key) => {
            const p = personOf(s.people, key);
            const sub = !p ? "Kimseye yazılmaz" : p.owner ? "Defter sahibi" : ROLE[p.role ?? "editor"];
            return (
              <button key={key} type="button" role="radio" aria-checked={who === key} disabled={save.isPending} onClick={() => pick(key)}
                className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50">
                <KisiNokta k={key} people={s.people} size={32} />
                <span className="min-w-0 flex-1"><span className="block font-medium [overflow-wrap:anywhere]">{key === ORTAK ? "Ortak (herkes)" : s.adOf(key)}</span>
                  <span className="block text-xs text-stone-500">{sub}</span></span>
                {who === key && <Check size={18} className="text-blue-600 dark:text-blue-400" aria-hidden />}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-stone-500">Bu kartın ekstre işlemleri Kim ne harcadı raporunda bu kişiye yazılır. Tek tek işlemde ayrıca değiştirebilirsin.</p>
      </div>
    </Modal>
  );
}

const ROLE: Record<string, string> = { viewer: "Sadece görür", editor: "Görür ve ekler", full: "Tam yetki" };

// ---------------------------------------------------------------- İşlem penceresi: "Kim harcadı"

const GIDER = new Set(["gider", "harcama", "iade", "faiz_ucret"]);
const sourceOf = (r: Row): "manual" | "card" | "recurring" | null =>
  r.source === "manual" || r.source === "card" || r.source === "recurring" ? r.source : null;
/** sabit kalemde seçim kalemin kendisine (her ay); diğerlerinde kaydın id'si */
const targetId = (r: Row) => (r.source === "recurring" ? r.recurring_id ?? r.id : r.id);

/** "Kim harcadı" çipleri: kişiler + Ortak; elle seçildiyse "Otomatik" (sunucunun kuralına dön). */
export function TxnSpender({ row }: { row: Row }) {
  const s = useSpenders();
  const ui = useUI();
  const [cur, setCur] = useState<{ who: string; set: boolean } | null>(null);
  const src = sourceOf(row);
  const save = useSave((who: string | null) => call<{ who: string; set: boolean }>("PUT", "/api/spender",
    { source: src, id: targetId(row), who }), (r) => {
    setCur(r);
    ui.toast(r.set ? `${s.adaOf(r.who)} yazıldı` : `Otomatik: ${s.adaOf(r.who)} yazıldı`);
  });
  if (!s.on || !src || row.spender === undefined || row.split_of || row.deleted || row.debt_id || !GIDER.has(row.kind)) return null;
  const who = cur?.who ?? row.spender;
  const set = cur?.set ?? !!row.spender_set;
  if (!s.edit) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-stone-600 dark:text-stone-400" data-testid="islem-harcayan">
        <KisiNokta k={who} people={s.people} size={20} />Kim harcadı: {s.adOf(who)}
      </p>
    );
  }
  const note = src === "recurring" ? "Her ay bu kişiye yazılır." : "Kim ne harcadı raporunda bu kişiye yazılır.";
  return (
    <section data-testid="islem-harcayan" aria-labelledby={`harcayan-${row.uid}`} className="space-y-1.5">
      <h3 id={`harcayan-${row.uid}`} className="label">Kim harcadı</h3>
      <div role="group" aria-label="Kim harcadı" className="flex flex-wrap gap-2">
        {[...s.keys.slice(1), ORTAK].map((key) => (
          <button key={key} type="button" aria-pressed={who === key} disabled={save.isPending}
            onClick={() => (who === key && set ? undefined : save.mutate(key))}
            className={cx("inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border py-1 pr-3 pl-1 text-sm",
              who === key ? "border-blue-600 bg-blue-50 font-medium text-blue-800 dark:bg-blue-950/60 dark:text-blue-200" : "border-stone-300 dark:border-stone-700")}>
            <KisiNokta k={key} people={s.people} size={22} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{s.adOf(key)}</span>
          </button>
        ))}
        {set && (
          <button type="button" disabled={save.isPending} onClick={() => save.mutate(null)}
            className="inline-flex items-center gap-1.5 rounded-full border border-stone-300 px-3 py-1 text-sm dark:border-stone-700">
            <RefreshCw size={14} aria-hidden />Otomatik</button>
        )}
      </div>
      <p className="text-xs text-stone-500">{note}{set ? "" : " Şimdilik otomatik seçildi: ekleyene ya da kartın sahibine göre."}</p>
    </section>
  );
}
