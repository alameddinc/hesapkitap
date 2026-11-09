import { ChevronLeft, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "../format";
import { askKirli } from "../lib/ask";
import { DURUM } from "../lib/durum";

// Pencere yığını (UX denetimi 09.11, Bulgu 6; mobil Sheet.tsx ile aynı kural): ekranda AYNI ANDA TEK pencere görünür.
// Pencere içinden açılan pencere (kategori seçici, bölme, maaş değişikliği, onay) alttakini gizler — alttaki kapanmaz,
// durumu korunur — ve başlığının üstünde "‹ <alttakinin başlığı>" geri satırı çıkar; geri ya da × yalnız üsttekini kapatır.
// Esc yalnız en üsttekini kapatır. Sıra ilk çizim sırasıdır (aynı anda açılan iç içe pencerede dıştaki önce).
interface Kayit { seq: number; title: string; kirli: boolean }
const yigin = new Map<symbol, Kayit>();
const subs = new Set<() => void>();
let surum = 0;
const yay = () => { surum++; subs.forEach((f) => f()); };
const useYigin = () => useSyncExternalStore((f) => (subs.add(f), () => { subs.delete(f); }), () => surum);
let sayac = 0;
const sirali = () => [...yigin.entries()].sort((a, b) => a[1].seq - b[1].seq);

// kaydedilmemiş değişikliği olan pencere açıkken sayfa kapanırsa/yenilenirse tarayıcı sorar
let uyari = false;
function beforeUnload(e: BeforeUnloadEvent) {
  if ([...yigin.values()].some((k) => k.kirli)) { e.preventDefault(); e.returnValue = ""; }
}
function uyariKur() {
  if (uyari || typeof window === "undefined") return;
  uyari = true;
  window.addEventListener("beforeunload", beforeUnload);
}

/** Kaydedilmemiş değişiklikle kapatma: kirliyse "Değişiklikler kaydedilmedi" sorulur (Kaydet / Kaydetmeden çık / Vazgeç).
 *  Pencerenin kendi "Vazgeç" düğmesi de bunu kullanır. Kaydet seçilirse onSave (yoksa hiçbir şey; pencere açık kalır). */
export async function kirliyseSor(dirty: boolean, onClose: () => void, onSave?: () => unknown): Promise<void> {
  if (!dirty) return onClose();
  const c = await askKirli({ baslik: DURUM.kirliBaslik, metin: DURUM.kirliMetin, kaydet: DURUM.kirliKaydet, cik: DURUM.kirliCik, vazgec: DURUM.kirliVazgec });
  if (c === "cik") onClose();
  else if (c === "kaydet") await onSave?.();
}

/** Açık pencere var mı (test ve yeni sürüm yenilemesi için). */
export const acikPencere = () => yigin.size > 0;

/** Mobilde alttan açılan panel, masaüstünde ortada modal.
 *  dirty: kaydedilmemiş değişiklik var; her kapatma yolu (×, arka plan, Esc, geri) "Değişiklikler kaydedilmedi" sorar.
 *  onSave: o sorudaki "Kaydet" (verilmezse soru yalnız Kaydetmeden çık / Vazgeç'e iner — Kaydet yine formu kaydetmeyi dener).
 *  noBack: geri satırı çizilmez (onay pencereleri: kendi Vazgeç düğmeleri var). */
export default function Modal({ title, onClose, children, footer, dirty = false, onSave, noBack = false, hidden = false, back }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** (uyumluluk, 4120ce0 adım modu) üstte bu pencerenin bir adımı açık: elle gizle. Yığın bunu zaten kendisi yapar. */
  hidden?: boolean;
  /** (uyumluluk) adım penceresi: kendi geri satırı ("‹ <önceki>", onClick); verilirse yığının geri satırı yerine bu çizilir */
  back?: { label: string; onClick: () => void };
  dirty?: boolean;
  onSave?: () => unknown;
  noBack?: boolean;
}) {
  const [me] = useState(() => Symbol("modal"));
  const [seq] = useState(() => ++sayac);
  const close = useRef(onClose);
  const save = useRef(onSave);
  const kirli = useRef(dirty);
  useEffect(() => { close.current = onClose; save.current = onSave; kirli.current = dirty; });
  useYigin();

  // kayıt: başlık/kirli değişince güncellenir
  useLayoutEffect(() => {
    yigin.set(me, { seq, title, kirli: dirty });
    if (dirty) uyariKur();
    yay();
  }, [me, seq, title, dirty]);
  useLayoutEffect(() => () => { yigin.delete(me); yay(); }, [me]);

  /** Kapatma isteği: kirliyse sor. */
  const sorup = useRef(false);
  const kapat = async () => {
    if (!kirli.current) return close.current();
    if (sorup.current) return;
    sorup.current = true;
    try { await kirliyseSor(true, () => close.current(), () => save.current?.()); } finally { sorup.current = false; }
  };
  const kapatRef = useRef(kapat);
  useEffect(() => { kapatRef.current = kapat; });

  const liste = sirali();
  const sira = liste.findIndex(([k]) => k === me);
  const ustte = !hidden && (sira === -1 ? true : sira === liste.length - 1);
  const alttaki = sira > 0 ? liste[sira - 1]![1] : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const l = sirali();
      if (l[l.length - 1]?.[0] === me) void kapatRef.current();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [me]);

  // gövdeye taşınır: iç içe pencerede alttaki gizlenince üstteki de gizlenmesin (DOM'da onun içinde değil)
  return createPortal(
    // alttaki pencere görünmez ve dokunulmaz (visibility + inert): DOM'da kalır, yazılanlar ve kaydırma korunur
    <div className="fixed inset-0 z-50 flex items-end justify-center text-left text-base font-normal tracking-normal text-stone-900 normal-case sm:items-center dark:text-stone-100"
      style={ustte ? undefined : { visibility: "hidden", pointerEvents: "none" }} inert={!ustte || undefined}
      role="dialog" aria-modal="true" aria-label={title} aria-hidden={!ustte || undefined} data-modal-ustte={ustte ? "1" : "0"}>
      <div className="absolute inset-0 bg-black/40" role="presentation" onClick={() => void kapat()} /* Esc ile de kapanır */ />
      <div className="relative flex max-h-[calc(100dvh-var(--sat)-0.5rem)] w-full flex-col rounded-t-2xl bg-white shadow-xl sm:max-w-lg sm:rounded-2xl dark:bg-stone-900">
        <div className="border-b border-stone-200 px-4 py-2 dark:border-stone-800">
          {back ? (
            <button type="button" className="-ml-2 flex min-h-11 items-center gap-0.5 rounded-lg px-2 text-sm font-medium text-blue-700 hover:bg-stone-100 dark:text-blue-400 dark:hover:bg-stone-800"
              onClick={back.onClick} aria-label={`Geri: ${back.label}`} data-testid="pencere-geri">
              <ChevronLeft size={18} aria-hidden /><span className="truncate">{back.label}</span>
            </button>
          ) : alttaki && !noBack && (
            <button type="button" className="-ml-2 flex min-h-11 items-center gap-0.5 rounded-lg px-2 text-sm font-medium text-blue-700 hover:bg-stone-100 dark:text-blue-400 dark:hover:bg-stone-800"
              onClick={() => void kapat()} aria-label={`Geri: ${alttaki.title}`} data-testid="sayfa-geri">
              <ChevronLeft size={18} aria-hidden /><span className="truncate">{alttaki.title}</span>
            </button>
          )}
          <div className="flex min-h-11 items-center justify-between gap-2">
            <h2 className="min-w-0 font-semibold">{title}</h2>
            <button className="btn-ghost -mr-2 size-11 justify-center p-0" onClick={() => void kapat()} aria-label="Kapat">
              <X size={18} />
            </button>
          </div>
        </div>
        <div className={cx("overflow-y-auto px-4 pt-4", footer ? "pb-4" : "pb-[max(1rem,var(--sab))]")}>{children}</div>
        {footer && (
          <div className="flex flex-wrap items-center gap-2 border-t border-stone-200 px-4 py-3 pb-[max(0.75rem,var(--sab))] dark:border-stone-800">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
