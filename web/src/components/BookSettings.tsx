import { useQuery } from "@tanstack/react-query";
import { Copy, KeyRound, Trash2, Users2 } from "lucide-react";
import { useState } from "react";
import { dateTR, maskText } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Rules { hidden_cards: string[]; hidden_accounts: number[]; hide_fees: boolean; hidden_pages: string[] }
interface Book {
  members: { id: number; username: string; last_login: string | null; disabled: boolean }[];
  invites: { code: string; note: string | null; expires_at: string | null }[];
  rules: Rules; cards: { card_no: string; bank: string }[]; accounts: { id: number; name: string; kind: string }[];
  pages: Record<string, string>;
}

const call = async <T,>(url: string, method = "GET", body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};
const link = (code: string) => `${window.location.origin}/#/kayit?kod=${code}`;
const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/** Ayarlar → Ortak defter: eşini davet et, neyi göremeyeceğini seç. */
export default function BookSettings() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["book"], queryFn: () => call<Book>("/api/book"), retry: false });
  const [note, setNote] = useState("");
  const [shown, setShown] = useState<{ title: string; text: string } | null>(null);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const invite = useSave(() => call<{ code: string }>("/api/book/invites", "POST", { note }), (i) => {
    setNote(""); setShown({ title: "Davet bağlantısı (14 gün geçerli, tek kullanımlık)", text: link(i.code) });
  });
  const delInvite = useSave((code: string) => call(`/api/book/invites/${code}`, "DELETE"));
  const setRules = useSave((r: Partial<Rules>) => call("/api/book/rules", "PUT", r));
  const remove = useSave((id: number) => call(`/api/book/members/${id}`, "DELETE"), () => ui.toast("Üye çıkarıldı"));
  const reset = useSave((id: number) => call<{ temp_password: string }>(`/api/book/members/${id}/reset-password`, "POST"),
    (r) => setShown({ title: "Geçici şifre (bir kez gösterilir; ilk girişte değiştirilir)", text: r.temp_password }));
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => ui.toast("Kopyalandı"), () => {});
  const d = q.data;
  if (!d) return null;
  const r = d.rules;
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Users2 size={18} /> Ortak defter</h2>
      <p className="text-sm text-stone-500">
        Eşini (ya da aileden birini) davet et: kendi kullanıcı adı ve şifresiyle girer, <b>bu defteri</b> görür, fiş ve harcama
        ekler, kategori düzeltir. Ekstre yükleme, silme ve ayarlar sende kalır. Aşağıda seçtiklerin ona hiç görünmez; toplamlara da girmez.
      </p>

      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <input className="input min-w-0 flex-1" placeholder="Not (ör. Eşim)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn-primary" disabled={invite.isPending} onClick={() => invite.mutate(undefined, onErr)}>Davet oluştur</button>
        </div>
        {shown && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
            {shown.title}
            <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all text-xs">{shown.text}</code>
              <button className="btn-ghost px-2 py-1" onClick={() => copy(shown.text)}><Copy size={14} /></button></div>
          </div>
        )}
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {d.members.map((m) => (
            <li key={m.id} className="flex items-center gap-2 py-2">
              <span className="flex-1"><b>{maskText(m.username)}</b>
                <span className="text-xs text-stone-500"> · {m.last_login ? `son giriş ${dateTR(m.last_login.slice(0, 10))}` : "henüz girmedi"}</span></span>
              <button className="btn-ghost px-2 py-1 text-xs" title="Şifre sıfırla" onClick={() => reset.mutate(m.id, onErr)}><KeyRound size={14} /></button>
              <button className="btn-danger px-2 py-1 text-xs" title="Çıkar"
                onClick={() => confirm(`${m.username} ortak defterden çıkarılsın mı? Eklediği kayıtlar defterde kalır.`) && remove.mutate(m.id, onErr)}>
                <Trash2 size={14} /></button>
            </li>
          ))}
          {d.invites.map((i) => (
            <li key={i.code} className="flex items-center gap-2 py-2 text-stone-500">
              <span className="flex-1">Davet {i.note ? `(${i.note})` : ""} · {i.expires_at ? `${dateTR(i.expires_at)}'e kadar` : ""}</span>
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => copy(link(i.code))}><Copy size={14} /> Bağlantı</button>
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => delInvite.mutate(i.code, onErr)}><Trash2 size={14} /></button>
            </li>
          ))}
          {!d.members.length && !d.invites.length && <li className="py-2 text-stone-500">Henüz üye yok.</li>}
        </ul>
      </div>

      <div className="space-y-2 border-t border-stone-100 pt-3 text-sm dark:border-stone-800">
        <h3 className="font-medium">Üyeye görünmesin</h3>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={r.hide_fees} onChange={(e) => setRules.mutate({ hide_fees: e.target.checked }, onErr)} />
          Faiz, gecikme ve kart ücretleri (faiz alarmı dahil)
        </label>
        {d.cards.length > 0 && <div>
          <div className="text-xs text-stone-500">Kartlar: işlemleri, ekstreleri, tahminleri</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {d.cards.map((c) => (
              <label key={c.card_no} className="flex items-center gap-2">
                <input type="checkbox" checked={r.hidden_cards.includes(c.card_no)}
                  onChange={() => setRules.mutate({ hidden_cards: toggle(r.hidden_cards, c.card_no) }, onErr)} />
                {c.bank} ··{c.card_no.slice(-4)}
              </label>
            ))}
          </div>
        </div>}
        {d.accounts.length > 0 && <div>
          <div className="text-xs text-stone-500">Hesaplar: bakiyesi ve hareketleri</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {d.accounts.map((a) => (
              <label key={a.id} className="flex items-center gap-2">
                <input type="checkbox" checked={r.hidden_accounts.includes(a.id)}
                  onChange={() => setRules.mutate({ hidden_accounts: toggle(r.hidden_accounts, a.id) }, onErr)} />
                {a.name}
              </label>
            ))}
          </div>
        </div>}
        <div>
          <div className="text-xs text-stone-500">Sayfalar (Kurallar ve Ekstreler zaten kapalı)</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(d.pages).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2">
                <input type="checkbox" checked={r.hidden_pages.includes(k)}
                  onChange={() => setRules.mutate({ hidden_pages: toggle(r.hidden_pages, k) }, onErr)} />
                {label}
              </label>
            ))}
          </div>
        </div>
        <p className="text-xs text-stone-500">Tek bir işlemi gizlemek için: işleme dokun → <b>Ortak defterde gizle</b>.</p>
      </div>
    </section>
  );
}
