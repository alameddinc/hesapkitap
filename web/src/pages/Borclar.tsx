import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import Debts, { type Debt } from "../components/Debts";
import Kpi from "../components/Kpi";

/** Borç / alacak defteri: kim kime ne (TL, döviz, altın) borçlu. Gelir/gider değil, eldeki parayı etkiler. */
export default function Borclar() {
  const debts = useQuery({ queryKey: ["debts"], queryFn: () => send<Debt[]>("/api/debts") });
  const people = useQuery({
    queryKey: ["transfers"],
    queryFn: () => send<{ people: { name: string }[] }>("/api/transfers"),
  });
  if (!debts.data) return <div className="text-sm text-stone-500">Yükleniyor…</div>;
  const openTry = debts.data.filter((d) => !d.settled_at && d.asset === "TRY");
  const sum = (dir: Debt["direction"]) => openTry.filter((d) => d.direction === dir).reduce((a, d) => a + d.remaining, 0);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Borç / Alacak</h1>
        <p className="text-sm text-stone-500">
          Borç verip almak gelir ya da gider değildir: verdiğin gün elindeki para azalır, geri aldığın gün artar; aylık
          harcaman değişmez. Geri gelmeyecekse "Vazgeçtim" de, kalan o gün gider olur.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Kpi label="Alacağın (TL)" value={sum("alacak")} tone="green" sub="sana borçlu olanlar" />
        <Kpi label="Borcun (TL)" value={sum("borc")} tone="red" sub="senin borçlu oldukların" />
      </div>
      <Debts debts={debts.data} people={(people.data?.people ?? []).map((p) => p.name)} />
    </div>
  );
}
