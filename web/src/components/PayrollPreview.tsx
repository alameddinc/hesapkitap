import { useQuery } from "@tanstack/react-query";
import { monthLabel, tl } from "../format";

const tl0 = (x: number) => tl(Math.round(x)).replace(",00", "");

interface PayrollMonth {
  month: number;
  gross: number;
  sgk: number;
  unemployment: number;
  income_tax: number;
  stamp: number;
  net: number;
  bracket_rate: number;
}

/** Brüt → net ay ay döküm (2026 yasal parametreleri). */
export default function PayrollPreview({ gross, year }: { gross: number; year: number }) {
  const q = useQuery({
    queryKey: ["payroll", gross, year],
    queryFn: () => fetch(`/api/payroll/net?gross=${gross}&year=${year}`).then((r) => r.json() as Promise<{
      params_year: number; months: PayrollMonth[]; annual_net: number;
    }>),
    enabled: gross > 0,
  });
  if (!(gross > 0)) return null;
  const d = q.data;
  return (
    <div className="rounded-lg border border-stone-200 text-xs dark:border-stone-700">
      <div className="max-h-56 overflow-y-auto">
        <table className="w-full tabular-nums whitespace-nowrap [overflow-wrap:normal]">
          <thead className="sticky top-0 bg-stone-100 dark:bg-stone-800">
            <tr className="text-left">
              <th className="px-2 py-1 font-medium">Ay</th>
              <th className="px-2 py-1 text-right font-medium">Net</th>
              <th className="px-2 py-1 text-right font-medium">Vergi</th>
              <th className="hidden px-2 py-1 text-right font-medium sm:table-cell">SGK+İşsiz.</th>
              <th className="px-2 py-1 text-right font-medium">Dilim</th>
            </tr>
          </thead>
          <tbody>
            {d?.months.map((m) => (
              <tr key={m.month} className="border-t border-stone-100 dark:border-stone-800">
                <td className="px-2 py-1">{monthLabel(`${year}-${String(m.month).padStart(2, "0")}`, true)}</td>
                <td className="px-2 py-1 text-right font-semibold">{tl0(m.net)}</td>
                <td className="px-2 py-1 text-right text-stone-500">{tl0(m.income_tax)}</td>
                <td className="hidden px-2 py-1 text-right text-stone-500 sm:table-cell">{tl0(m.sgk + m.unemployment)}</td>
                <td className="px-2 py-1 text-right text-stone-500">%{Math.round(m.bracket_rate * 100)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d && (
        <p className="border-t border-stone-100 px-2 py-1.5 text-stone-500 dark:border-stone-800">
          Yıllık net {tl(d.annual_net)} · {d.params_year} parametreleri (vergi dilimleri, asgari ücret istisnası, SGK tavanı).
          {d.params_year !== year && ` ${year} parametreleri henüz yok; ${d.params_year} kullanıldı.`}
          {" "}BES, sendika, icra gibi kesintiler dahil değil.
        </p>
      )}
    </div>
  );
}
