import { cx } from "../format";

export default function Segmented<T extends string>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; tone?: "red" | "green" }[];
}) {
  return (
    <div className="flex rounded-lg bg-stone-100 p-1 dark:bg-stone-800" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            value === o.value
              ? cx("bg-white shadow-sm dark:bg-stone-700",
                  o.tone === "red" && "text-red-600 dark:text-red-400",
                  o.tone === "green" && "text-green-700 dark:text-green-400")
              : "text-stone-500",
          )}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
