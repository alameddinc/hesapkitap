import { useQuery } from "@tanstack/react-query";
import { Bell, Check, Copy, DatabaseBackup, Download, KeyRound, Lock, Send } from "lucide-react";
import { useState } from "react";
import { useSave } from "../components/hooks";
import { cx, dateTR } from "../format";
import { useUI } from "../ui";

interface ConfigItem {
  name: string;
  label: string;
  secret: boolean;
  source: "env" | "app" | "none";
  value_hint: string | null;
  default?: string;
}

const HELP: Record<string, React.ReactNode> = {
  GEMINI_API_KEY: <>Fiş, fatura ve tanınmayan banka ekstrelerini okumak için. <a className="text-blue-600 underline" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com/apikey</a> · Faturalandırmayı açarsan verilerin model eğitiminde kullanılmaz.</>,
  GEMINI_MODEL: <>Varsayılan: gemini-3.1-flash-lite (en ucuz; fiş başına ~0,1 cent).</>,
  TELEGRAM_BOT_TOKEN: <>Telegram'da <b>@BotFather</b> → <code>/newbot</code> → verdiği token.</>,
  BACKUP_PASSPHRASE: <>Telegram'a giden yedeğin şifresi. <b>Kaybetme</b>: yedek bu olmadan açılamaz.</>,
};

function KeyRow({ c }: { c: ConfigItem }) {
  const ui = useUI();
  const [value, setValue] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const save = useSave((v: string | null) => fetch("/api/settings/config", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: c.name, value: v }),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); }), () => { setValue(""); ui.toast("Kaydedildi"); });
  const test = useSave(() => post<{ detail: string }>(`/api/settings/config/test/${c.name}`), (r) => ui.toast(r.detail));
  const gen = useSave(() => post<{ passphrase: string }>("/api/settings/config/generate-backup-passphrase"), (r) => setShown(r.passphrase));
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const locked = c.source === "env";
  const testable = c.name === "GEMINI_API_KEY" || c.name === "TELEGRAM_BOT_TOKEN";

  return (
    <li className="space-y-1.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{c.label}</span>
        <span className={cx("rounded px-1.5 py-0.5 text-xs",
          c.source === "none" ? "bg-stone-100 text-stone-500 dark:bg-stone-800" : "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300")}>
          {c.source === "env" ? `sunucu .env · ${c.value_hint}` : c.source === "app" ? `kayıtlı · ${c.value_hint}` : c.default ? `varsayılan · ${c.default}` : "girilmedi"}
        </span>
      </div>
      <p className="text-xs text-stone-500">{HELP[c.name]}</p>
      {locked ? (
        <p className="text-xs text-stone-500"><Lock size={12} className="inline" /> Sunucudaki ortam değişkeninden geliyor; oradan değiştirilir.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <input className="input min-w-0 flex-1" type={c.secret ? "password" : "text"} autoComplete="off" value={value}
            placeholder={c.source === "app" ? "Değiştirmek için yeni değeri yapıştır" : "Yapıştır"} onChange={(e) => setValue(e.target.value)} />
          <button className="btn-primary" disabled={!value.trim()} onClick={() => save.mutate(value, onErr)}>Kaydet</button>
          {c.source === "app" && <button className="btn-ghost" onClick={() => confirm("Silinsin mi?") && save.mutate(null, onErr)}>Sil</button>}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {testable && c.source !== "none" && (
          <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" onClick={() => test.mutate(undefined, onErr)}>
            <Check size={14} /> Test et
          </button>
        )}
        {c.name === "BACKUP_PASSPHRASE" && !locked && (
          <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
            onClick={() => (c.source === "none" || confirm("Yeni şifre üretilsin mi? Eski yedekler eski şifreyle açılır.")) && gen.mutate(undefined, onErr)}>
            <KeyRound size={14} /> Güçlü şifre üret
          </button>
        )}
      </div>
      {shown && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          Bu şifreyi şimdi bir şifre yöneticisine kaydet, bir daha gösterilmeyecek:
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 break-all">{shown}</code>
            <button className="btn-ghost px-2 py-1" onClick={() => navigator.clipboard.writeText(shown).then(() => ui.toast("Kopyalandı"))}><Copy size={14} /></button>
          </div>
        </div>
      )}
    </li>
  );
}

function PasswordForm() {
  const ui = useUI();
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const save = useSave(async () => {
    const r = await fetch("/api/settings/password", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: cur, new: nw }) });
    if (!r.ok) throw new Error((await r.json()).detail);
  }, () => { setCur(""); setNw(""); ui.toast("Şifre değişti; diğer cihazlarda yeniden giriş gerekir"); });
  return (
    <section className="card space-y-2 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Şifre</h2>
      <div className="grid gap-2 sm:grid-cols-3">
        <input className="input" type="password" placeholder="Mevcut şifre" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        <input className="input" type="password" placeholder="Yeni şifre (10+ karakter)" autoComplete="new-password" value={nw} onChange={(e) => setNw(e.target.value)} />
        <button className="btn-primary justify-center" disabled={!cur || nw.length < 10}
          onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Değiştir</button>
      </div>
    </section>
  );
}

interface SettingsStatus {
  telegram: { token: boolean; connected: boolean };
  backup: { encrypted_offsite: boolean; files: { name: string; size: number; created: string }[]; last_run: string | null };
  last_reminder: string | null;
}

async function post<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "POST" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
}

function Step({ n, done, children }: { n: number; done: boolean; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className={cx("flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
        done ? "bg-green-600 text-white" : "bg-stone-200 dark:bg-stone-700")}>{done ? "✓" : n}</span>
      <div className="text-sm">{children}</div>
    </li>
  );
}

export default function Settings() {
  const ui = useUI();
  const st = useQuery({ queryKey: ["settings"], queryFn: () => fetch("/api/settings").then((r) => r.json() as Promise<SettingsStatus>) });
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const connect = useSave(() => post<{ name: string }>("/api/settings/telegram/connect"), (r) => ui.toast(`Bağlandı: ${r.name}`));
  const test = useSave(() => post<{ sent: number }>("/api/settings/telegram/test"), (r) => ui.toast(`${r.sent} mesaj gönderildi`));
  const disconnect = useSave(() => post("/api/settings/telegram/disconnect"), () => ui.toast("Bağlantı kaldırıldı"));
  const backupNow = useSave(() => post<{ path: string }>("/api/settings/backup/now"), (r) => ui.toast(`Yedek alındı: ${r.path}`));
  const s = st.data;
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => fetch("/api/settings/config").then((r) => r.json() as Promise<ConfigItem[]>) });
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ayarlar</h1>

      <section className="card p-4">
        <h2 className="flex items-center gap-2 font-semibold"><KeyRound size={18} /> Anahtarlar</h2>
        <p className="text-xs text-stone-500">Değerler bu sunucuda saklanır; ekranda sadece son 4 karakteri görünür.</p>
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {cfg.data?.map((c) => <KeyRow key={c.name} c={c} />)}
        </ul>
      </section>

      <section className="card space-y-3 p-4">
        <h2 className="flex items-center gap-2 font-semibold"><Bell size={18} /> Telegram hatırlatmaları</h2>
        <p className="text-sm text-stone-500">
          Son ödemeden 3 gün, 1 gün önce ve son gün sabah 09:00'da mesaj; kesim tarihinden 3 gün sonra "ekstreyi yükle";
          her ayın 1'inde geçen ay özeti. Günlük şifreli yedek de buraya gelir.
        </p>
        {s && (
          <ol className="space-y-3">
            <Step n={1} done={s.telegram.token}>
              Telegram'da <b>@BotFather</b>'a <code>/newbot</code> yaz, verdiği token'ı yukarıdaki <b>Anahtarlar → Telegram bot token</b>{" "}
              alanına yapıştır.
              {!s.telegram.token && <div className="mt-1 text-amber-600">Token henüz girilmedi.</div>}
            </Step>
            <Step n={2} done={s.telegram.connected}>
              Oluşturduğun bota Telegram'dan <code>/start</code> yaz, sonra:
              <div className="mt-2">
                <button className="btn-primary" disabled={!s.telegram.token || connect.isPending}
                  onClick={() => connect.mutate(undefined, onErr)}>
                  {s.telegram.connected ? "Yeniden bağla" : "Bağla"}
                </button>
              </div>
            </Step>
            <Step n={3} done={!!s.last_reminder}>
              <div className="flex flex-wrap gap-2">
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={!s.telegram.connected}
                  onClick={() => test.mutate(undefined, onErr)}><Send size={14} /> Test mesajı gönder</button>
                {s.telegram.connected && (
                  <button className="btn-danger" onClick={() => disconnect.mutate(undefined, onErr)}>Bağlantıyı kaldır</button>
                )}
              </div>
            </Step>
          </ol>
        )}
      </section>

      <section className="card space-y-3 p-4">
        <h2 className="flex items-center gap-2 font-semibold"><DatabaseBackup size={18} /> Yedekler</h2>
        <p className="text-sm text-stone-500">
          Her gece 03:00'te otomatik; son 30 gün sunucuda tutulur.
          {s?.backup.encrypted_offsite && s.telegram.connected
            ? " Şifreli kopyası Telegram'a gönderiliyor."
            : " Sunucu dışı şifreli kopya için Telegram'ı bağla ve yukarıda yedek şifresi belirle."}
        </p>
        <button className="btn-primary" disabled={backupNow.isPending} onClick={() => backupNow.mutate(undefined, onErr)}>
          Şimdi yedek al
        </button>
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {s?.backup.files.map((f) => (
            <li key={f.name} className="flex items-center justify-between gap-2 py-2">
              <span>{dateTR(f.created)} <span className="text-xs text-stone-500">· {(f.size / 1024).toFixed(0)} KB</span></span>
              <a className="btn-ghost px-2 py-1" href={`/api/settings/backup/${f.name}`} download><Download size={14} /> İndir</a>
            </li>
          ))}
          {s && !s.backup.files.length && <li className="py-2 text-stone-500">Henüz yedek yok</li>}
        </ul>
      </section>

      {auth.data?.password_source === "app" && <PasswordForm />}
    </div>
  );
}
