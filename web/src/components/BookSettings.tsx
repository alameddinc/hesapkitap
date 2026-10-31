import { useQuery } from "@tanstack/react-query";
import { detailText } from "../lib/apiError";
import { ChevronDown, Copy, EyeOff, KeyRound, Trash2, Users2 } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, maskText, tsDateTR } from "../format";
import { useUI } from "../ui";
import { type BookRole, useLoginKind, useSave } from "./hooks";
import { usePlan } from "./Plan";

interface Rules { hidden_cards: string[]; hidden_accounts: number[]; hide_fees: boolean; hidden_pages: string[] }
interface Member { id: number; username: string; last_login: string | null; disabled: boolean; role: BookRole; own_rules: boolean; rules: Rules }
interface Book {
  members: Member[];
  invites: { code: string; note: string | null; expires_at: string | null; book_role?: BookRole | null }[];
  rules: Rules; cards: { card_no: string; bank: string }[]; accounts: { id: number; name: string; kind: string }[];
  pages: Record<string, string>; roles?: Record<string, string>;
}

const ROLES: { value: BookRole; label: string; sub: string }[] = [
  { value: "viewer", label: "Sadece görür", sub: "Her şeyi görür, bir şey ekleyemez." },
  { value: "editor", label: "Görür ve ekler", sub: "Fiş ve harcama ekler, kategori düzeltir; silemez." },
  { value: "full", label: "Tam yetki", sub: "Ekstre yükler, düzenler, siler. Ayarlar ve üyeler sende kalır." },
];

const call = async <T,>(url: string, method = "GET", body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(detailText(j.detail, r.status));
  return j as T;
};
const link = (code: string) => `${window.location.origin}/#/kayit?kod=${code}`;
const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
const hiddenCount = (r: Rules) => r.hidden_cards.length + r.hidden_accounts.length + r.hidden_pages.length + (r.hide_fees ? 1 : 0);

/** Gizleme kuralları düzenleyici: ortak kurallar ya da tek bir üyeye özel kurallar. */
function RulesEditor({ d, r, onChange }: { d: Book; r: Rules; onChange: (p: Partial<Rules>) => void }) {
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={r.hide_fees} onChange={(e) => onChange({ hide_fees: e.target.checked })} />
        Faiz, gecikme ve kart ücretleri (faiz alarmı dahil)
      </label>
      {d.cards.length > 0 && <div>
        <div className="text-xs text-stone-500">Kartlar: işlemleri, ekstreleri, tahminleri</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {d.cards.map((c) => (
            <label key={c.card_no} className="flex items-center gap-2">
              <input type="checkbox" checked={r.hidden_cards.includes(c.card_no)}
                onChange={() => onChange({ hidden_cards: toggle(r.hidden_cards, c.card_no) })} />
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
                onChange={() => onChange({ hidden_accounts: toggle(r.hidden_accounts, a.id) })} />
              {a.name}
            </label>
          ))}
        </div>
      </div>}
      <div>
        <div className="text-xs text-stone-500">Sayfalar (Kurallar ve Ekstreler tam yetki dışında zaten kapalı)</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {Object.entries(d.pages).map(([k, label]) => (
            <label key={k} className="flex items-center gap-2">
              <input type="checkbox" checked={r.hidden_pages.includes(k)}
                onChange={() => onChange({ hidden_pages: toggle(r.hidden_pages, k) })} />
              {label}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Üye satırı: yetki seçici; dokununca "Bu kişiden neleri gizle". */
function MemberRow({ m, d }: { m: Member; d: Book }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const setRole = useSave((role: BookRole) => call(`/api/book/members/${m.id}`, "PATCH", { book_role: role }), () => ui.toast("Yetki değişti"));
  const setRules = useSave((r: Partial<Rules>) => call(`/api/book/rules?member=${m.id}`, "PUT", r));
  const toShared = useSave(() => call(`/api/book/members/${m.id}/rules`, "DELETE"), () => ui.toast("Ortak kurallar geçerli"));
  const remove = useSave(() => call(`/api/book/members/${m.id}`, "DELETE"), () => ui.toast("Üye çıkarıldı"));
  const reset = useSave(() => call<{ temp_password: string }>(`/api/book/members/${m.id}/reset-password`, "POST"),
    (r) => setShown(r.temp_password));
  const n = hiddenCount(m.rules);
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className="flex min-w-0 flex-1 items-center gap-1 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="min-w-0">
            <b className="break-all">{maskText(m.username)}</b>
            <span className="block text-xs text-stone-500">
              {m.last_login ? `son giriş ${tsDateTR(m.last_login)}` : "henüz girmedi"}
              {" · "}{m.own_rules ? "kendine özel kural" : "ortak kurallar"}{n ? ` (${n} gizli)` : ""}
            </span>
          </span>
          <ChevronDown size={14} className={cx("shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
        </button>
        <select className="input w-auto py-1 text-xs" aria-label="Yetki" value={m.role} disabled={setRole.isPending}
          onChange={(e) => setRole.mutate(e.target.value as BookRole, onErr)}>
          {ROLES.map((r) => <option key={r.value} value={r.value}>{d.roles?.[r.value] ?? r.label}</option>)}
        </select>
        <button className="btn-ghost px-2 py-1 text-xs" title="Şifre sıfırla" onClick={() => reset.mutate(undefined, onErr)}><KeyRound size={14} /></button>
        <button className="btn-danger px-2 py-1 text-xs" title="Çıkar"
          onClick={() => confirm(`${m.username} ortak defterden çıkarılsın mı? Eklediği kayıtlar defterde kalır.`) && remove.mutate(undefined, onErr)}>
          <Trash2 size={14} /></button>
      </div>
      {shown && (
        <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          Geçici şifre (bir kez gösterilir; ilk girişte değiştirilir)
          <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all text-xs">{shown}</code>
            <button className="btn-ghost px-2 py-1" onClick={() => navigator.clipboard.writeText(shown).then(() => ui.toast("Kopyalandı"), () => {})}>
              <Copy size={14} /></button></div>
        </div>
      )}
      {open && (
        <div className="mt-2 space-y-2 rounded-lg bg-stone-50 p-3 dark:bg-stone-900">
          <h4 className="flex items-center gap-1.5 font-medium"><EyeOff size={14} /> Bu kişiden neleri gizle</h4>
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2">
              <input type="radio" name={`rules-${m.id}`} checked={!m.own_rules}
                onChange={() => toShared.mutate(undefined, onErr)} />
              Ortak kuralları kullan
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name={`rules-${m.id}`} checked={m.own_rules}
                onChange={() => setRules.mutate(m.rules, onErr)} /* ortak kurallardan başlar */ />
              Bu kişiye özel
            </label>
          </div>
          {m.own_rules
            ? <RulesEditor d={d} r={m.rules} onChange={(p) => setRules.mutate(p, onErr)} />
            : <p className="text-xs text-stone-500">Aşağıdaki ortak kurallar geçerli{n ? ` (${n} gizli)` : ""}. Değiştirmek için <b>Bu kişiye özel</b>.</p>}
        </div>
      )}
    </li>
  );
}

/** Ayarlar → Ortak defter: eşini davet et, yetkisini ve neyi göremeyeceğini seç. */
export default function BookSettings() {
  const ui = useUI();
  const plan = usePlan();
  const email = useLoginKind() === "email"; // hazır sürüm: üye e-postayla girer
  const q = useQuery({ queryKey: ["book"], queryFn: () => call<Book>("/api/book"), retry: false });
  const [note, setNote] = useState("");
  const [role, setRole] = useState<BookRole>("editor");
  const [shown, setShown] = useState<string | null>(null);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const invite = useSave(() => call<{ code: string }>("/api/book/invites", "POST", { note, book_role: role }), (i) => {
    setNote(""); setShown(link(i.code));
  });
  const delInvite = useSave((code: string) => call(`/api/book/invites/${code}`, "DELETE"));
  const setRules = useSave((r: Partial<Rules>) => call("/api/book/rules", "PUT", r));
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => ui.toast("Kopyalandı"), () => {});
  const d = q.data;
  if (!d) return null;
  const roleLabel = (r?: string | null) => (r ? d.roles?.[r] ?? ROLES.find((x) => x.value === r)?.label : undefined);
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Users2 size={18} /> Ortak defter</h2>
      <p className="text-sm text-stone-500">
        Eşini (ya da aileden birini) davet et: kendi {email ? "e-posta adresi" : "kullanıcı adı"} ve şifresiyle girer, <b>bu defteri</b> görür. Ne
        yapabileceğini yetkisi belirler. Gizlediklerin ona hiç görünmez; toplamlara da girmez. Ayrıca kendine ait özel bir defteri olur.
      </p>

      <div className="space-y-2">
        {plan?.features.ortak_defter === false ? (
          <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
            Davet etmek <b>Aile</b> paketinde. Ayarlar → Paketin bölümüne bak.</p>
        ) : (
          <div className="space-y-2">
            <fieldset className="grid gap-1.5 sm:grid-cols-3">
              <legend className="mb-1 text-xs text-stone-500">Yetki</legend>
              {ROLES.map((r) => (
                <label key={r.value} aria-label={r.label} className={cx("flex cursor-pointer gap-2 rounded-lg border p-2 text-sm",
                  role === r.value ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30" : "border-stone-200 dark:border-stone-700")}>
                  <input type="radio" name="invite-role" className="mt-0.5" checked={role === r.value} onChange={() => setRole(r.value)} />
                  <span><b className="font-medium">{roleLabel(r.value)}</b><span className="block text-xs text-stone-500">{r.sub}</span></span>
                </label>
              ))}
            </fieldset>
            <div className="flex flex-wrap gap-2">
              <input className="input min-w-0 flex-1" placeholder="Not (ör. Eşim)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="btn-primary" disabled={invite.isPending} onClick={() => invite.mutate(undefined, onErr)}>Davet oluştur</button>
            </div>
          </div>
        )}
        {shown && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
            Davet bağlantısı (14 gün geçerli, tek kullanımlık)
            <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all text-xs">{shown}</code>
              <button className="btn-ghost px-2 py-1" onClick={() => copy(shown)}><Copy size={14} /></button></div>
          </div>
        )}
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {d.members.map((m) => <MemberRow key={m.id} m={m} d={d} />)}
          {d.invites.map((i) => (
            <li key={i.code} className="flex items-center gap-2 py-2 text-stone-500">
              <span className="flex-1">Davet {i.note ? `(${i.note})` : ""}{roleLabel(i.book_role) ? ` · ${roleLabel(i.book_role)}` : ""}
                {i.expires_at ? ` · ${dateTR(i.expires_at)}'e kadar` : ""}</span>
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => copy(link(i.code))}><Copy size={14} /> Bağlantı</button>
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => delInvite.mutate(i.code, onErr)}><Trash2 size={14} /></button>
            </li>
          ))}
          {!d.members.length && !d.invites.length && <li className="py-2 text-stone-500">Henüz üye yok.</li>}
        </ul>
        {d.members.length > 0 && <p className="text-xs text-stone-500">Üyeye dokun: ondan neleri gizleyeceğini seç.</p>}
      </div>

      <div className="space-y-2 border-t border-stone-100 pt-3 text-sm dark:border-stone-800">
        <h3 className="font-medium">Ortak kurallar: görünmesin</h3>
        <p className="text-xs text-stone-500">Kendine özel kuralı olmayan herkese.</p>
        <RulesEditor d={d} r={d.rules} onChange={(p) => setRules.mutate(p, onErr)} />
        <p className="text-xs text-stone-500">Tek bir işlemi gizlemek için: işleme dokun → <b>Ortak defterde gizle</b>.</p>
      </div>
    </section>
  );
}
