import { useState } from "react";
import { cx } from "../format";
import {
  bookHint, bookOverride, canChooseBook, openBookOf, savedElsewhere, spenderField, spenderKeys, targetIsShared, targetViewer,
  type BookKey,
} from "../lib/saveBook";
import { useUI } from "../ui";
import { useBook, useMeta, useSwitchBook, type BookPerson } from "./hooks";
import Segmented from "./Segmented";
import { KisiNokta, ORTAK } from "./Spender";
import { useLock } from "./Plan";

// Fiş / fatura / elle kayıt onayında "Deftere" (Ortak · Benim) ve "Kim harcadı" seçimi (Aile; mobil ile aynı metinler).
// Sunucu yeteneği "save_book_choice": istek X-HK-Book ile hedef deftere gider (lib/saveBook). "Kim harcadı": yetenek
// "spender_assign", paket satırı "aile_rapor" açık, hedef ortak defter, kişi sadece görür değil. Varsayılan Sen: hiçbir şey
// gönderilmez (sunucu ekleyene yazar).

export interface SaveTarget {
  /** seçim görünsün mü (üye + yetenek) */
  choose: boolean;
  open: BookKey | null;
  target: BookKey | null;
  setTarget: (b: BookKey) => void;
  /** istekte gönderilecek defter (açık defterden farklıysa) */
  book: BookKey | null;
  /** hedef ortak defterde yalnız görme yetkisi: kaydedemez */
  viewer: boolean;
  /** "Kim harcadı" çipleri açık mı */
  spenderOn: boolean;
  spender: string | null;
  setSpender: (k: string) => void;
  people: BookPerson[];
  me: number | null | undefined;
  /** kayıt gövdesine eklenecek alanlar ({spender}) */
  extra: () => { spender?: string };
  /** kaydedildikten sonra: diğer deftere gittiyse "Aç" düğmeli bildirim gösterip true döner */
  announce: () => boolean;
}

export function useSaveTarget(): SaveTarget {
  const b = useBook().data;
  const meta = useMeta().data;
  const ui = useUI();
  const go = useSwitchBook();
  const lock = useLock("aile_rapor");
  const caps = meta?.capabilities;
  const choose = canChooseBook(caps, b);
  const open = openBookOf(b);
  const [picked, setTarget] = useState<BookKey | null>(null);
  // sadece görür üye ortak deftere yazamaz: varsayılan "Benim" (mobil ile aynı)
  const target = choose ? picked ?? (b?.member && b.role === "viewer" ? "kendi" : open) : open;
  const book = bookOverride(target, open);
  const viewer = targetViewer(target, b);
  const people = b?.people ?? [];
  const me = b?.me;
  const spenderOn = !!caps?.includes("spender_assign") && !lock && !viewer && targetIsShared(target, b) && people.length > 1;
  const [spender, setSpender] = useState<string | null>(null);
  return {
    choose, open, target, setTarget, book, viewer, spenderOn, spender, setSpender, people, me,
    extra: () => (spenderOn ? spenderField(spender, me) : {}),
    announce: () => {
      const msg = savedElsewhere(target, open);
      if (!msg || !target) return false;
      ui.toast(msg, { action: { label: "Aç", run: () => go(target) } });
      return true;
    },
  };
}

/** "Deftere" Ortak · Benim ve açıklaması. */
export function BookChoice({ t, onChange }: { t: SaveTarget; onChange?: (b: BookKey) => void }) {
  if (!t.choose || !t.target) return null;
  return (
    <div data-testid="deftere">
      <span className="label">Deftere</span>
      <Segmented label="Deftere" value={t.target}
        onChange={(v) => { if (v !== t.target) { t.setTarget(v); onChange?.(v); } }}
        options={[{ value: "ortak", label: "Ortak" }, { value: "kendi", label: "Benim" }]} />
      <p className="mt-1 text-xs text-stone-500">
        {bookHint(t.target)}
        {t.viewer && <span className="text-amber-700 dark:text-amber-400"> Ortak defterde yalnız görme yetkin var; Benim'e kaydedebilirsin.</span>}
      </p>
    </div>
  );
}

/** "Kim harcadı" çipleri (Sen · diğerleri · Ortak); seçilmediyse Sen. */
export function SaveSpender({ t }: { t: SaveTarget }) {
  if (!t.spenderOn) return null;
  const mine = `u${t.me}`;
  const cur = t.spender ?? mine;
  const name = (k: string) => {
    if (k === ORTAK) return "Ortak";
    if (k === mine) return "Sen";
    return t.people.find((p) => `u${p.id}` === k)?.name ?? "Kişi";
  };
  return (
    <section data-testid="kayit-harcayan" aria-labelledby="kayit-harcayan" className="space-y-1.5">
      <h3 id="kayit-harcayan" className="label">Kim harcadı</h3>
      <div role="group" aria-label="Kim harcadı" className="flex flex-wrap gap-2">
        {spenderKeys(t.people, t.me).map((key) => (
          <button key={key} type="button" aria-pressed={cur === key} onClick={() => t.setSpender(key)}
            className={cx("inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border py-1 pr-3 pl-1 text-sm",
              cur === key ? "border-blue-600 bg-blue-50 font-medium text-blue-800 dark:bg-blue-950/60 dark:text-blue-200" : "border-stone-300 dark:border-stone-700")}>
            <KisiNokta k={key} people={t.people} size={22} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{name(key)}</span>
          </button>
        ))}
      </div>
      <p className="text-xs text-stone-500">Kim ne harcadı raporunda bu kişiye yazılır.</p>
    </section>
  );
}
