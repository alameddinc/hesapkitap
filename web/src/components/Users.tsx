import { useQuery } from "@tanstack/react-query";
import { Copy, Trash2, UserPlus, Users as UsersIcon } from "lucide-react";
import { useState } from "react";
import { cx, dateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface UserRow {
  id: number; username: string; role: "admin" | "user"; created_at: string; last_login: string | null;
  disabled: boolean; must_change_password: boolean; ai_used: number; ai_quota: number | null; ai_quota_custom: number | null;
}
interface Invite { code: string; note: string | null; created_at: string; expires_at: string | null; used_by_name: string | null; used_at: string | null }
interface AdminData { users: UserRow[]; invites: Invite[]; default_quota: number }

async function call<T>(url: string, method = "POST", body?: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
}

const inviteLink = (code: string) => `${window.location.origin}/#/kayit?kod=${code}`;

/** Yönetici: hesap aç/kapat, davet, şifre sıfırla, yapay zekâ kotası. Kullanıcıların finans verisi burada görünmez. */
export default function Users() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => call<AdminData>("/api/admin/users", "GET") });
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const [note, setNote] = useState("");
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<{ title: string; text: string } | null>(null);
  const invite = useSave(() => call<Invite>("/api/admin/invites", "POST", { note }), (i) => {
    setNote("");
    setSecret({ title: "Davet bağlantısı (14 gün geçerli, tek kullanımlık)", text: inviteLink(i.code) });
  });
  const create = useSave(() => call<{ temp_password: string }>("/api/admin/users", "POST", { username: name }), (r) => {
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
        Fiş okuma senin Gemini anahtarınla, kişi başı aylık {d?.default_quota ?? 100} okuma; kendi anahtarını giren sınırsız.
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        <div className="flex gap-2">
          <input className="input min-w-0 flex-1" placeholder="Not (ör. Ayşe)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn-primary" disabled={invite.isPending} onClick={() => invite.mutate(undefined, onErr)}>Davet oluştur</button>
        </div>
        <div className="flex gap-2">
          <input className="input min-w-0 flex-1" placeholder="Kullanıcı adı" autoCapitalize="none" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={name.trim().length < 3}
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
                {u.must_change_password && <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">şifre bekliyor</span>}
              </span>
              <span className="text-xs text-stone-500">
                {u.last_login ? `son giriş ${dateTR(u.last_login.slice(0, 10))}` : "hiç girmedi"}
                {u.ai_quota !== null && <> · yapay zekâ {u.ai_used}/{u.ai_quota}</>}
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
                <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                  onClick={() => confirm(`${u.username} için yeni geçici şifre üretilsin mi? Açık oturumları kapanır.`) && reset.mutate(u, onErr)}>
                  Şifre sıfırla
                </button>
                <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                  onClick={() => patch.mutate({ id: u.id, body: { disabled: !u.disabled } }, onErr)}>
                  {u.disabled ? "Aç" : "Kapat"}
                </button>
                <button className="btn-danger px-2 py-1"
                  onClick={() => confirm(`${u.username} silinsin mi? Giriş yapamaz; veri dosyası sunucuda arşive taşınır.`) && del.mutate(u, onErr)}>
                  <Trash2 size={14} /> Sil
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

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
                  <button className="btn-ghost px-2 py-1 text-xs" aria-label="Daveti sil" onClick={() => delInvite.mutate(i.code, onErr)}><Trash2 size={14} /></button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
