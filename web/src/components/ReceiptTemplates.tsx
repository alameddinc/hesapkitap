import { useQuery } from "@tanstack/react-query";
import { Receipt } from "lucide-react";
import { send } from "../lib/apiError";

/** GET /api/admin/receipt-templates: zincir / akaryakıt fişi şablonları. Kişisel veri yok: ad yalnız bilinen zincirse. */
interface Tpl {
  id: number; name: string; signature: "VKN" | "unvan"; fuel: boolean; single: boolean; source: string;
  uses: number; fails: number; disabled: boolean; last_used: string | null; created_at: string; shareable: boolean;
}
interface Overview { templates: Tpl[]; total: number; active: number; uses: number }

const day = (s: string | null) => (s ? s.slice(0, 16).replace("T", " ") : "—");

/** Ayarlar (yönetici): fiş şablonları — yapay zekâya gitmeden okunan fişler, şablon başına isabet / hata. */
export default function ReceiptTemplates() {
  const q = useQuery({ queryKey: ["admin-receipt-templates"], retry: false,
    queryFn: () => send<Overview>("/api/admin/receipt-templates") });
  if (q.error) return <section className="card p-4 text-sm text-red-600">Fiş şablonları: {(q.error as Error).message}</section>;
  const s = q.data;
  if (!s) return null;
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Receipt size={18} /> Fiş şablonları</h2>
      <p className="text-xs text-stone-500">
        Aynı işyerinin fişi telefon metninden yapay zekâsız okunur. {s.total} şablon, {s.active} açık, toplam {s.uses} okuma.
      </p>
      {s.templates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-stone-500">
              <tr><th className="py-1 pr-2">Şablon</th><th className="pr-2">İmza</th><th className="pr-2 text-right">İsabet</th>
                <th className="pr-2 text-right">Hata</th><th className="pr-2">Son kullanım</th><th>Durum</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {s.templates.map((t) => (
                <tr key={t.id}>
                  <td className="py-1.5 pr-2 font-medium">
                    {t.name}{t.fuel && <span className="ml-1 text-stone-500">{t.single ? "· slip" : "· akaryakıt"}</span>}
                  </td>
                  <td className="pr-2">{t.signature}{t.source === "community" ? " · topluluk" : ""}</td>
                  <td className="pr-2 text-right tabular-nums">{t.uses}</td>
                  <td className="pr-2 text-right tabular-nums">{t.fails}</td>
                  <td className="pr-2 tabular-nums text-stone-500">{day(t.last_used)}</td>
                  <td>{t.disabled
                    ? <span className="rounded bg-red-100 px-1.5 text-red-800 dark:bg-red-950 dark:text-red-300">kapalı</span>
                    : <span className="rounded bg-green-100 px-1.5 text-green-800 dark:bg-green-950 dark:text-green-300">açık</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
