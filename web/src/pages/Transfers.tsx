import { useQuery } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, ChevronDown, Handshake } from "lucide-react";
import { useMemo, useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import { useMeta, useSave } from "../components/hooks";
import Kpi from "../components/Kpi";
import { cx, dateTR, maskText, tl } from "../format";
import { useUI } from "../ui";

interface Person {
  name: string;
  sent: number;
  received: number;
  net: number;
  count: number;
  last_date: string;
  category: string | null;
  track_debt: number;
}
interface Item {
  id: number;
  entry_date: string;
  description: string;
  amount: number;
  kind: "gelir" | "gider";
  category: string;
  counterparty: string;
  note: string | null;
}
interface Own {
  id: number;
  transfer_date: string;
  amount: number;
  account: string | null;
  description: string | null;
}
interface Data { people: Person[]; own: Own[]; items: Item[] }

const shortBank = (b: string | null) => (b ? b.replace(/\s+(KATILIM\s+)?BANKASI.*$/i, "").replace(/\s+A\.?Ş\.?$/i, "") : "Hesap");

const put = (name: string, body: object) =>
  fetch(`/api/transfers/people/${encodeURIComponent(name)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then((r) => r.json() as Promise<{ updated: number }>);

/** Havale/FAST karşı tarafları: kime ne gitti, kimden ne geldi; kişi bazında kategori ve borç takibi. */
export default function Transfers() {
  const ui = useUI();
  const meta = useMeta();
  const q = useQuery({ queryKey: ["transfers"], queryFn: () => fetch("/api/transfers").then((r) => r.json() as Promise<Data>) });
  const [open, setOpen] = useState<string | null>(null);
  const save = useSave(({ name, body }: { name: string; body: object }) => put(name, body), (r) => {
    if (r.updated) ui.toast(`${r.updated} hareketin kategorisi güncellendi`);
  });
  const options = useMemo(() => [...new Set([...(meta.data?.categories ?? []), ...(meta.data?.income_categories ?? []),
    "Aile", "Borç / Alacak", "Kira & Aidat"])], [meta.data]);

  if (!q.data) return <div className="text-sm text-stone-500">Yükleniyor…</div>;
  const { people, own, items } = q.data;
  const sent = people.reduce((s, p) => s + p.sent, 0);
  const received = people.reduce((s, p) => s + p.received, 0);
  const ownIn = own.filter((o) => o.amount > 0).reduce((s, o) => s + o.amount, 0);
  const debts = people.filter((p) => p.track_debt);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Transferler</h1>
        <p className="text-sm text-stone-500">
          Hesap dökümlerindeki havale/FAST'ler kişi kişi. Bir kişiye kategori verirsen o kişiye giden/gelen tüm
          hareketler o kategoriye geçer. Kendi hesapların arası aktarımlar gelir/gider sayılmaz, aşağıda ayrı durur.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label="Kişilere giden" value={sent} tone="red" />
        <Kpi label="Kişilerden gelen" value={received} tone="green" />
        <Kpi label="Kendi hesapların arası" value={ownIn} sub="toplama katılmaz" />
      </div>

      {debts.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-2 flex items-center gap-2 font-semibold"><Handshake size={18} /> Borç / alacak takibi</h2>
          <ul className="space-y-1 text-sm">
            {debts.map((p) => (
              <li key={p.name} className="flex justify-between gap-2">
                <span>{maskText(p.name)}</span>
                <span className={cx("tabular-nums", p.net > 0 ? "text-amber-700 dark:text-amber-400" : p.net < 0 ? "text-green-700 dark:text-green-400" : "text-stone-500")}>
                  {p.net === 0 ? "ödeşik" : p.net > 0 ? `sen ${tl(p.net)} borçlusun` : `sana ${tl(-p.net)} borçlu`}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-stone-500">Net = gelen − giden. Kişiye giden para alacak, gelen para borç sayılır.</p>
        </section>
      )}

      <section className="card divide-y divide-stone-100 dark:divide-stone-800">
        {people.length === 0 && (
          <p className="p-4 text-sm text-stone-500">Henüz transfer yok. Ekstreler → Hesap hareketleri'nden banka dökümü (PDF/Excel) yükle.</p>
        )}
        {people.map((p) => (
          <div key={p.name}>
            <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setOpen(open === p.name ? null : p.name)}>
              <div className="min-w-0 flex-1">
                <div className="font-medium">{maskText(p.name)}</div>
                <div className="text-xs text-stone-500">{p.count} hareket · son {dateTR(p.last_date)}{p.category && ` · ${p.category}`}</div>
              </div>
              <div className="text-right text-sm tabular-nums">
                {p.sent > 0 && <div className="flex items-center justify-end gap-1"><ArrowUpRight size={14} className="text-red-500" />{tl(p.sent)}</div>}
                {p.received > 0 && <div className="flex items-center justify-end gap-1 text-green-700 dark:text-green-400"><ArrowDownLeft size={14} />{tl(p.received)}</div>}
              </div>
              <ChevronDown size={16} className={cx("text-stone-400 transition-transform", open === p.name && "rotate-180")} />
            </button>
            {open === p.name && (
              <div className="space-y-3 px-4 pb-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <span className="label">Kategori (tüm hareketlerine uygulanır)</span>
                    <CategoryPicker value={p.category ?? ""} options={options}
                      onChange={(v) => save.mutate({ name: p.name, body: { category: v } })} />
                  </div>
                  <label className="flex items-center gap-2 self-end pb-2 text-sm">
                    <input type="checkbox" checked={!!p.track_debt}
                      onChange={(e) => save.mutate({ name: p.name, body: { track_debt: e.target.checked } })} />
                    Borç / alacak olarak takip et
                  </label>
                </div>
                <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
                  {items.filter((i) => i.counterparty === p.name).map((i) => (
                    <li key={i.id} className="flex items-center gap-2 px-3 py-2">
                      <span className="w-20 shrink-0 text-xs text-stone-500">{dateTR(i.entry_date)}</span>
                      <span className="min-w-0 flex-1 truncate">{maskText(i.description)}</span>
                      <span className="hidden text-xs text-stone-500 sm:inline">{i.category}</span>
                      <span className={cx("tabular-nums", i.kind === "gelir" && "text-green-700 dark:text-green-400")}>
                        {tl(i.kind === "gelir" ? i.amount : -Math.abs(i.amount))}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </section>

      {own.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><ArrowLeftRight size={18} /> Kendi hesapların arası</h2>
          <p className="mb-2 text-xs text-stone-500">Gelir/gider toplamlarına katılmaz; sadece para nereden nereye geçmiş görmen için.</p>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {own.map((o) => (
              <li key={o.id} className="flex items-center gap-2 py-1.5">
                <span className="w-20 shrink-0 text-xs text-stone-500">{dateTR(o.transfer_date)}</span>
                <span className="min-w-0 flex-1 truncate">{shortBank(o.account)} {o.amount > 0 ? "← giriş" : "→ çıkış"}</span>
                <span className={cx("tabular-nums", o.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(o.amount)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
