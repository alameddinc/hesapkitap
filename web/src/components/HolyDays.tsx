import { useQuery } from "@tanstack/react-query";
import { Moon } from "lucide-react";
import { useEffect, useState } from "react";
import { dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Holy {
  days: { key: string; name: string; date: string; days_left: number; hijri: string }[];
  in_ramadan: boolean;
  fitre: { amount: number; people: number; total: number };
}

/** Ramazan, Ramazan Bayramı, Kurban Bayramı (yaklaşık) ve fitre hesabı. */
export default function HolyDays() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["holy"], queryFn: () => fetch("/api/zakat/holy").then((r) => r.json() as Promise<Holy>) });
  const [amount, setAmount] = useState("");
  const [people, setPeople] = useState("");
  useEffect(() => {
    if (!q.data) return;
    setAmount(q.data.fitre.amount ? String(q.data.fitre.amount) : "");
    setPeople(String(q.data.fitre.people));
  }, [q.data]);
  const save = useSave(() => fetch("/api/zakat/settings", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fitre_amount: parseAmount(amount) || 0, fitre_people: Number(people) || 0 }),
  }), () => ui.toast("Fitre kaydedildi"));
  const h = q.data;
  if (!h) return null;
  const total = (parseAmount(amount) || 0) * (Number(people) || 0);
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Moon size={18} /> Ramazan, fitre ve kurban</h2>
      <ul className="grid gap-2 text-sm sm:grid-cols-3">
        {h.days.map((d) => (
          <li key={d.key} className="rounded-lg bg-stone-50 p-2 dark:bg-stone-800/50">
            <div className="font-medium">{d.name}</div>
            <div className="text-xs text-stone-500">≈ {dateTR(d.date)} · {d.days_left === 0 ? "bugün" : `${d.days_left} gün`}</div>
            <div className="text-xs text-stone-400">{d.hijri}</div>
          </li>
        ))}
      </ul>
      <p className="text-xs text-stone-500">Tarihler hesapla bulunur, Diyanet takviminden 1 gün sapabilir. Yaklaşınca Özet'te ve Telegram'da hatırlatılır.</p>
      <div className="flex flex-wrap items-end gap-2 text-sm">
        <label className="text-xs">Kişi başı fitre (₺)
          <input className="input mt-1 w-28 text-right" inputMode="decimal" value={amount} placeholder="Diyanet" onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className="text-xs">Kişi sayısı
          <input className="input mt-1 w-20 text-right" inputMode="numeric" value={people} onChange={(e) => setPeople(e.target.value)} />
        </label>
        <span className="pb-2">= <b>{tl(total)}</b></span>
        <button className="btn-primary px-3 py-1.5 text-sm" disabled={save.isPending} onClick={() => save.mutate(undefined)}>Kaydet</button>
      </div>
      <p className="text-xs text-stone-500">Fitre: bayram sabahı fakir olmayan herkes için, çocuklar dahil (velisi verir). Kişi başı tutarı Diyanet her yıl açıklar.</p>
    </section>
  );
}
