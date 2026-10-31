import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { call as apiCall } from "../lib/apiError";
import { Copy, Trash2, UserPlus, Users as UsersIcon } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, tsDateTR } from "../format";
import { useUI } from "../ui";
import { isEmail, useLoginKind, useSave } from "./hooks";

interface UserRow {
  id: number; username: string; role: "admin" | "user"; created_at: string; last_login: string | null;
  disabled: boolean; must_change_password: boolean; ai_used: number; ai_quota: number | null; ai_quota_custom: number | null;
  plan?: "free" | "premium" | "aile"; plan_until?: string | null; plan_source?: string | null;
  deleted_at?: string | null; delete_after?: string | null; // kullanıcı sildi, geri alma süresinde
}
interface Invite { code: string; note: string | null; created_at: string; expires_at: string | null; used_by_name: string | null; used_at: string | null }
interface AdminData { users: UserRow[]; invites: Invite[]; default_quota: number; hosted?: boolean }

const call = <T,>(url: string, method = "POST", body?: unknown) => apiCall<T>(method, url, body);

const inviteLink = (code: string) => `${window.location.origin}/#/kayit?kod=${code}`;

/** Yönetici: hesap aç/kapat, davet, şifre sıfırla, yapay zekâ kotası. Kullanıcıların finans verisi burada görünmez. */
export default function Users() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => call<AdminData>("/api/admin/users", "GET") });
  const onErr = { onError: (e: Error) => ui.error(e.message) };
  const [note, setNote] = useState("");
  const [name, setName] = useState("");
  const email = useLoginKind() === "email"; // hazır sürüm: hesap e-postayla açılır
  const [secret, setSecret] = useState<{ title: string; text: string } | null>(null);
  const invite = useSave(() => call<Invite>("/api/admin/invites", "POST", { note }), (i) => {
    setNote("");
    setSecret({ title: "Davet bağlantısı (14 gün geçerli, tek kullanımlık)", text: inviteLink(i.code) });
  });
  const create = useSave(() => call<{ temp_password: string }>("/api/admin/users", "POST", { username: name.trim() }), (r) => {
    setSecret({ title: `${name} için geçici şifre (ilk girişte değiştirecek)`, text: r.temp_password });
    setName("");
  });
  const patch = useSave((a: { id: number; body: object }) => call(`/api/admin/users/${a.id}`, "PATCH", a.body));
  const reset = useSave((u: UserRow) => call<{ temp_password: string }>(`/api/admin/users/${u.id}/reset-password`)
    .then((r) => ({ ...r, u })), (r) => setSecret({ title: `${r.u.username} için yeni geçici şifre`, text: r.temp_password }));
  const del = useSave((u: UserRow) => call(`/api/admin/users/${u.id}`, "DELETE"), () => ui.toast("Hesap silindi; veri dosyası arşive taşındı"));
  const delInvite = useSave((code: string) => call(`/api/admin/invites/${code}`, "DELETE"));
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => ui.toast("Kopyalandı"));
  const d = q.data;
  const openInvites = d?.invites.filter((i) => !i.used_at) ?? [];

  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><UsersIcon size={18} /> Kullanıcılar</h2>
      <p className="text-xs text-stone-500">
        Her kullanıcının verisi ayrı dosyada; buradan sadece hesapları yönetirsin, kimsenin harcamasını göremezsin.
        {d?.hosted ? "Belge okuma hakkı pakete göre: Ücretsiz ayda 50, Premium ve Aile ayda 1000 (ailede ortak)."
          : `Fiş okuma senin Gemini anahtarınla, kişi başı aylık ${d?.default_quota ?? 100} okuma; kendi anahtarını giren sınırsız.`}
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        <div className="flex gap-2">
          <input className="input min-w-0 flex-1" placeholder="Not (ör. Ayşe)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn-primary" disabled={invite.isPending} onClick={() => invite.mutate(undefined, onErr)}>Davet oluştur</button>
        </div>
        <div className="flex gap-2">
          <input className="input min-w-0 flex-1" type={email ? "email" : "text"} aria-label={email ? "E-posta" : "Kullanıcı adı"}
            placeholder={email ? "E-posta (ornek@gmail.com)" : "Kullanıcı adı"} autoCapitalize="none" autoCorrect="off" spellCheck={false}
            value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={email ? !isEmail(name) : name.trim().length < 3}
            onClick={() => create.mutate(undefined, onErr)}><UserPlus size={14} /> Hesap aç</button>
        </div>
      </div>

      {secret && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          {secret.title} — bir daha gösterilmez, şimdi gönder:
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 break-all">{secret.text}</code>
            <button className="btn-ghost px-2 py-1" aria-label="Kopyala" onClick={() => copy(secret.text)}><Copy size={14} /></button>
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setSecret(null)}>Kapat</button>
          </div>
        </div>
      )}

      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {d?.users.map((u) => (
          <li key={u.id} className="space-y-1.5 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className={cx("font-medium", u.disabled && "text-stone-400 line-through")}>
                {u.username}
                {u.role === "admin" && <span className="ml-1.5 rounded bg-stone-100 px-1.5 py-0.5 text-xs dark:bg-stone-800">yönetici</span>}
                {u.delete_after && <span className="ml-1.5 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-800 dark:bg-red-950 dark:text-red-300">
                  silinecek · {dateTR(u.delete_after.slice(0, 10))}</span>}
                {u.must_change_password && <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">şifre bekliyor</span>}
              </span>
              <span className="text-xs text-stone-500">
                {u.last_login ? `son giriş ${tsDateTR(u.last_login)}` : "hiç girmedi"}
                {u.ai_quota !== null && <> · {d?.hosted ? "belge okuma" : "yapay zekâ"} {u.ai_used}/{u.ai_quota}</>}
                {u.plan && u.plan !== "free" && u.role !== "admin" && (
                  <> · <b className="text-amber-700 dark:text-amber-400">{u.plan === "aile" ? "Aile" : "Premium"}</b>
                    {u.plan_until && ` ${dateTR(u.plan_until.slice(0, 10))}'e kadar`}
                    {u.plan_source === "manual" ? " (sen verdin)" : u.plan_source === "revenuecat" ? " (mağaza)" : ""}</>
                )}
              </span>
            </div>
            {u.role !== "admin" && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <label className="flex items-center gap-1">Kota
                  <input className="input w-20 px-2 py-1 text-xs" type="number" min={0} defaultValue={u.ai_quota ?? ""}
                    onBlur={(e) => {
                      const v = e.target.value === "" ? null : Number(e.target.value);
                      if (v !== u.ai_quota) patch.mutate({ id: u.id, body: v === null ? { reset_quota: true } : { ai_quota: v } }, onErr);
                    }} />
                </label>
                {d?.hosted && (
                  <select className="input w-auto px-2 py-1 text-xs" value="" aria-label="Paket ver"
                    onChange={async (e) => {
                      const [plan, days] = e.target.value.split(":");
                      if (plan && (await ask(plan === "free" ? `${u.username} ücretsiz pakete dönsün mü?` : `${u.username} için ${days} gün ${plan === "aile" ? "Aile" : "Premium"} verilsin mi?`)))
                        patch.mutate({ id: u.id, body: { plan, plan_days: days ? Number(days) : undefined } }, onErr);
                    }}>
                    <option value="">Paket ver…</option>
                    <option value="premium:30">Premium · 30 gün</option>
                    <option value="premium:90">Premium · 90 gün</option>
                    <option value="premium:365">Premium · 1 yıl</option>
                    <option value="aile:90">Aile · 90 gün</option>
                    <option value="free">Ücretsize döndür</option>
                  </select>
                )}
                <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                  onClick={() => void ask(`${u.username} için yeni geçici şifre üretilsin mi? Açık oturumları kapanır.`).then((ok) => { if (ok) reset.mutate(u, onErr); })}>
                  Şifre sıfırla
                </button>
                <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                  onClick={() => patch.mutate({ id: u.id, body: { disabled: !u.disabled } }, onErr)}>
                  {u.disabled ? "Aç" : "Kapat"}
                </button>
                <button className="btn-danger px-2 py-1" disabled={del.isPending}
                  onClick={() => void ask(`${u.username} silinsin mi? Giriş yapamaz; veri dosyası sunucuda arşive taşınır.`).then((ok) => { if (ok) del.mutate(u, onErr); })}>
                  <Trash2 size={14} /> Sil
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      <DeletedAccounts />

      {openInvites.length > 0 && (
        <div>
          <h3 className="text-sm font-medium">Bekleyen davetler</h3>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {openInvites.map((i) => (
              <li key={i.code} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span><code>{i.code}</code> {i.note && <span className="text-stone-500">· {i.note}</span>}
                  {i.expires_at && <span className="text-xs text-stone-500"> · {dateTR(i.expires_at)}'e kadar</span>}</span>
                <span className="flex gap-1">
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => copy(inviteLink(i.code))}><Copy size={14} /> Bağlantı</button>
                  <button className="btn-ghost px-2 py-1 text-xs" aria-label="Daveti sil" disabled={delInvite.isPending}
                    onClick={() => void ask("Davet silinsin mi?\n\nBağlantı artık çalışmaz.", { irreversible: false }).then((ok) => { if (ok) delInvite.mutate(i.code, onErr); })}><Trash2 size={14} /></button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

interface DeletedRow {
  user_id: number; deleted_at: string; delete_after: string | null; purged_at: string | null; reason: string | null;
  plan: string | null; plan_until: string | null; plan_source: string | null; plan_store: string | null; will_renew: number | null;
  store_customer: string | null; email_hash: string;
  notes: { at: string; event?: string; action?: string; warning?: string }[]; warnings: { at: string; warning: string }[];
}

const REASON: Record<string, string> = { self: "kendisi", owner: "defter sahibiyle", admin: "yönetici" };
const STORE: Record<string, string> = { app_store: "App Store", play_store: "Google Play" };

/** Silinen hesapların anonim kaydı (1 yıl): fatura / itiraz takibi. E-posta saklanmaz; aramada özeti eşleştirilir. */
function DeletedAccounts() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const list = useQuery({
    queryKey: ["admin-deleted", term], enabled: open,
    queryFn: () => call<{ records: DeletedRow[]; keep_days: number }>("/api/admin/deleted", "POST", { email: term || null }),
  });
  const rows = list.data?.records ?? [];
  const warned = rows.filter((r) => r.warnings.length).length;
  return (
    <details className="rounded-lg border border-stone-200 p-2 dark:border-stone-700" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm font-medium">Silinen hesaplar{warned ? <span className="ml-1.5 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-800 dark:bg-red-950 dark:text-red-300">{warned} uyarı</span> : null}</summary>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
        <input className="input min-w-0 flex-1" type="email" placeholder="E-postayla ara (saklanmaz, özeti eşleşir)" value={q}
          onChange={(e) => setQ(e.target.value)} autoCapitalize="none" spellCheck={false} />
        <button className="btn-ghost border border-stone-300 dark:border-stone-700">Ara</button>
        {term && <button type="button" className="btn-ghost text-xs" onClick={() => { setQ(""); setTerm(""); }}>Temizle</button>}
      </form>
      {list.isError && <p className="mt-2 text-sm text-red-600">{(list.error as Error).message}</p>}
      {open && list.data && !rows.length && <p className="mt-2 text-sm text-stone-500">{term ? "Bu e-postayla silinmiş hesap yok." : "Kayıt yok."}</p>}
      <ul className="mt-1 divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {rows.map((r) => (
          <li key={r.user_id} className="space-y-0.5 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span><b>#{r.user_id}</b> <code className="text-xs text-stone-500">{r.email_hash}…</code>
                <span className="text-xs text-stone-500"> · {REASON[r.reason ?? ""] ?? r.reason}</span></span>
              <span className="text-xs text-stone-500">
                silindi {tsDateTR(r.deleted_at)} · {r.purged_at ? `kalıcı ${tsDateTR(r.purged_at)}` : r.delete_after ? `geri alınabilir ${dateTR(r.delete_after)}'e kadar` : ""}
              </span>
            </div>
            {(r.plan && r.plan !== "free") && (
              <p className="text-xs text-stone-500">
                {r.plan === "aile" ? "Aile" : "Premium"} · {r.plan_source === "revenuecat" ? STORE[r.plan_store ?? ""] ?? "mağaza" : "elle"}
                {r.plan_until && ` · ${dateTR(r.plan_until)}'e kadar`}
                {r.will_renew != null && (r.will_renew ? " · yenileniyordu" : " · iptal edilmişti")}
              </p>
            )}
            {r.warnings.map((w, i) => (
              <p key={i} className="rounded bg-red-50 px-2 py-1 text-xs text-red-800 dark:bg-red-950/50 dark:text-red-300">⚠ {dateTR(w.at)} · {w.warning}</p>
            ))}
          </li>
        ))}
      </ul>
      {open && <p className="mt-1 text-xs text-stone-500">Kayıtlar kalıcı silmeden {list.data?.keep_days ?? 365} gün sonra düşer.</p>}
    </details>
  );
}
