import { useQuery } from "@tanstack/react-query";
import { tl } from "../format";

export interface AccountOpt { id: number; name: string; kind: string; current: number | null }

export const useAccounts = () => useQuery({
  queryKey: ["accounts"],
  queryFn: () => fetch("/api/accounts").then((r) => r.json() as Promise<{ accounts: AccountOpt[]; wallets: AccountOpt[] }>),
});

const LAST = "hk-last-account";
export const lastAccount = (): number | null => {
  try { const v = localStorage.getItem(LAST); return v ? Number(v) : null; } catch { return null; }
};
export const rememberAccount = (id: number | null) => {
  try { if (id) localStorage.setItem(LAST, String(id)); } catch { /* özel mod */ }
};

/** Banka / nakit hesabı seçimi (gelirin girdiği, giderin çıktığı hesap). Boş = belirtilmedi. */
export default function AccountSelect({ value, onChange, label, id, kinds }: {
  value: number | null; onChange: (v: number | null) => void; label: string; id?: string;
  /** yalnız bu türler (ör. ["banka", "nakit"]) */ kinds?: string[];
}) {
  const q = useAccounts();
  const accs = (q.data?.accounts ?? []).filter((a) => !kinds || kinds.includes(a.kind));
  return (
    <div>
      <label className="label" htmlFor={id}>{label}</label>
      <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{accs.length ? "Belirtme" : "Önce Hesaplar'dan hesap ekle"}</option>
        {accs.map((a) => <option key={a.id} value={a.id}>{a.name}{a.current != null ? ` · ${tl(a.current)}` : ""}</option>)}
      </select>
    </div>
  );
}
