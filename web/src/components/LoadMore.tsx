import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

/** Sonsuz kaydırma: göründüğünde sıradaki sayfayı ister; klavye/ekran okuyucu için düğme olarak da çalışır. */
export default function LoadMore({ onMore, busy, label }: { onMore: () => void; busy: boolean; label: string }) {
  const ref = useRef<HTMLButtonElement>(null);
  const more = useRef(onMore);
  useEffect(() => { more.current = onMore; }, [onMore]);
  // busy bitince yeniden gözlenir: yeni sayfa geldikten sonra düğme hâlâ görünüyorsa (kısa sayfa) sıradaki de istenir
  useEffect(() => {
    const el = ref.current;
    if (busy || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) more.current(); }, { rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [busy]);
  return (
    <button ref={ref} type="button" className="flex w-full items-center justify-center gap-2 py-3 text-sm text-stone-500 hover:text-stone-700 dark:hover:text-stone-300"
      disabled={busy} onClick={onMore}>
      {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Yükleniyor…</> : label}
    </button>
  );
}
