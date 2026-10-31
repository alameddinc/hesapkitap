import { useQuery } from "@tanstack/react-query";
import { call as apiCall } from "../lib/apiError";
import { HeartHandshake } from "lucide-react";
import { useUI } from "../ui";
import { useSave } from "./hooks";

export const DONATION_KINDS: Record<string, string> = {
  sadaka: "Sadaka", fitre: "Fitre", fidye: "Fidye", kurban: "Kurban", adak: "Adak", kefaret: "Kefaret", bagis: "Vakıf / dernek bağışı",
};

const call = (method: string, url: string, body?: unknown) => apiCall<any>(method, url, body);

/** Bir hareketi sadaka/bağış ya da zekât olarak işaretle (zekât → o yılın zekât ödemesi). */
export default function MarkDonation({ source, id }: { source: "entry" | "card"; id: number }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["donation-mark", source, id],
    queryFn: () => call("GET", `/api/donations/mark/${source}/${id}`) as Promise<{ mark: { kind: string; label: string } | null }> });
  const mark = useSave((kind: string) => kind
    ? call("POST", "/api/donations/mark", { source, id, kind })
    : call("DELETE", `/api/donations/mark/${source}/${id}`), () => ui.toast("Kaydedildi"));
  const cur = q.data?.mark?.kind ?? "";
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
      <HeartHandshake size={16} className="shrink-0 text-stone-500" />
      <span className="flex-1">Bu bir sadaka / zekât mı?</span>
      <select className="input w-auto px-2 py-1 text-sm" aria-label="Sadaka türü" value={cur}
        onChange={(e) => mark.mutate(e.target.value, { onError: (err) => ui.toast(err.message) })}>
        <option value="">Hayır</option>
        <option value="zekat">Zekât (bu yılın)</option>
        {Object.entries(DONATION_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
    </div>
  );
}
