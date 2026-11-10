import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import Debts, { type Debt } from "../components/Debts";
import Kpi from "../components/Kpi";
import SorguDurumu, { KartIskelet, SatirIskelet } from "../components/SorguDurumu";
import { ChevronRight, ListChecks } from "lucide-react";
import { useCap } from "../components/hooks";
import { useUI } from "../ui";

/** Borç / alacak defteri: kim kime ne (TL, döviz, altın) borçlu. Gelir/gider değil, eldeki parayı etkiler. */
export default function Borclar() {
  const ui = useUI();
  const plan = useCap("debt_plan");
  const debts = useQuery({ queryKey: ["debts"], queryFn: () => send<Debt[]>("/api/debts") });
  const people = useQuery({
    queryKey: ["transfers"],
    queryFn: () => send<{ people: { name: string }[] }>("/api/transfers"),
  });
  // açık TL alacak/borç toplamı sunucudan (net-worth: Özet ve Hesaplar ile aynı rakam); eski sunucuda satırlardan
  const nw = useQuery({ queryKey: ["net-worth"], queryFn: () => send<{ receivable?: number; payable?: number }>("/api/net-worth") });
  if (!debts.data) return <SorguDurumu q={debts} etiket="Borç ve alacaklar yükleniyor"><div className="space-y-3"><KartIskelet satir={1} /><SatirIskelet n={4} /></div></SorguDurumu>;
  const openTry = debts.data.filter((d) => !d.settled_at && d.asset === "TRY");
  const sum = (dir: Debt["direction"]) => (dir === "alacak" ? nw.data?.receivable : nw.data?.payable)
    ?? openTry.filter((d) => d.direction === dir).reduce((a, d) => a + d.remaining, 0);
  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">Borç / Alacak</h1>
          {plan && <button className="btn-ghost px-2 py-1 text-sm" onClick={() => ui.go("borc-plani")}><ListChecks size={16} /> Borç planı <ChevronRight size={14} /></button>}
        </div>
        <p className="text-sm text-stone-500">
          Borç verip almak gelir ya da gider değildir: verdiğin gün elindeki para azalır, geri aldığın gün artar; aylık
          harcaman değişmez. Geri gelmeyecekse "Alamayacağım" de, kalan o gün gider olur; karşı taraf borcunu sildiyse "Borcu sildi", kalan o gün gelir olur.
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
