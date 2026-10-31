import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { cx } from "../format";

// açık pencereler yığını: Esc yalnız en üsttekini kapatır (pencere içinde pencere: kalem düzenleme, önce/sonra)
const stack: symbol[] = [];

/** Mobilde alttan açılan panel, masaüstünde ortada modal. */
export default function Modal({ title, onClose, children, footer }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const me = Symbol("modal");
    stack.push(me);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && stack[stack.length - 1] === me && close.current();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      stack.splice(stack.indexOf(me), 1);
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center text-left text-base font-normal tracking-normal text-stone-900 normal-case sm:items-center dark:text-stone-100" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/40" role="presentation" onClick={onClose} /* Esc ile de kapanır */ />
      <div className="relative flex max-h-[calc(100dvh-var(--sat)-0.5rem)] w-full flex-col rounded-t-2xl bg-white shadow-xl sm:max-w-lg sm:rounded-2xl dark:bg-stone-900">
        <div className="flex items-center justify-between border-b border-stone-200 px-4 py-3 dark:border-stone-800">
          <h2 className="font-semibold">{title}</h2>
          <button className="btn-ghost -mr-2 p-2" onClick={onClose} aria-label="Kapat">
            <X size={18} />
          </button>
        </div>
        <div className={cx("overflow-y-auto px-4 pt-4", footer ? "pb-4" : "pb-[max(1rem,var(--sab))]")}>{children}</div>
        {footer && (
          <div className="flex items-center gap-2 border-t border-stone-200 px-4 py-3 pb-[max(0.75rem,var(--sab))] dark:border-stone-800">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
