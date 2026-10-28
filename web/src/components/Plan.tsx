import { Crown, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { dateTR } from "../format";
import { inApp } from "./AppServer";
import { useMeta } from "./hooks";

export const usePlan = () => useMeta().data?.plan;

/** Paket karşılaştırması (barındırılan sürüm). Zekât ve verini indirmek her pakette serbest. */
const ROWS: [string, string, string, string][] = [
  ["Yapay zekâ ile belge okuma", "ayda 3", "sınırsız", "sınırsız"],
  ["Fiyat takibi, en ucuz market", "—", "✓", "✓"],
  ["Ortak defter (eşin, ailen)", "—", "—", "✓"],
  ["Ekstre, fiş, özet, plan, kumbara, zekât", "✓", "✓", "✓"],
];

export function Upgrade({ need }: { need?: string }) {
  return (
    <p className="text-sm text-stone-600 dark:text-stone-400">
      {need ? <><b>{need}</b> paketinde var. </> : null}
      {inApp() ? "Abonelik yakında buradan alınabilecek." : "Abonelik HesapKitap telefon uygulamasından alınır (App Store / Google Play)."}
    </p>
  );
}

/** Paketinde olmayan bir özelliğin yerine: ne olduğunu ve hangi pakette olduğunu söyler. */
export function PlanGate({ feature, title, children }: { feature: string; title: string; children: ReactNode }) {
  const p = usePlan();
  if (!p || p.features[feature] !== false) return <>{children}</>;
  return (
    <section className="card space-y-2 p-6 text-center">
      <Crown size={32} className="mx-auto text-amber-500" />
      <h2 className="text-lg font-semibold">{title}</h2>
      <Upgrade need={feature === "ortak_defter" ? "Aile" : "Premium"} />
    </section>
  );
}

/** Ayarlar → Paketin (sadece barındırılan sürümde). */
export default function PlanSettings() {
  const p = usePlan();
  if (!p?.hosted) return null;
  return (
    <section className="card space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><Sparkles size={18} /> Paketin: {p.label}</h2>
        {p.until && <span className="text-xs text-stone-500">{dateTR(p.until.slice(0, 10))} tarihine kadar</span>}
      </div>
      {p.member && <p className="text-sm text-stone-500">Ortak defterdesin: defter sahibinin paketini kullanırsın.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-stone-500">
              <th className="py-1 font-normal" />
              {(["free", "premium", "aile"] as const).map((k) => (
                <th key={k} className={`px-2 py-1 text-center font-medium ${p.plan === k ? "text-blue-700 dark:text-blue-400" : ""}`}>
                  {{ free: "Ücretsiz", premium: "Premium", aile: "Aile" }[k]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
            {ROWS.map(([name, ...cells]) => (
              <tr key={name}>
                <td className="py-1.5 pr-2">{name}</td>
                {cells.map((c, i) => <td key={i} className="px-2 text-center tabular-nums">{c}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p.plan === "free" && <Upgrade />}
      <p className="text-xs text-stone-500">Verini indirmek ve hesabını silmek her pakette serbest.</p>
    </section>
  );
}
