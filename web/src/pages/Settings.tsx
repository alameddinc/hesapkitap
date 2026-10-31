import { useQuery } from "@tanstack/react-query";
import { detailText } from "../lib/apiError";
import { Bell, CalendarClock, Check, Copy, DatabaseBackup, Download, KeyRound, Lock, Mail, RefreshCw, Send } from "lucide-react";
import { useMeta } from "../components/hooks";
import { periodRange } from "../components/MonthPicker";
import { useState } from "react";
import { useSave } from "../components/hooks";
import { cx, dateTR, maskText } from "../format";
import { useUI } from "../ui";
import Users from "../components/Users";
import DataPrivacy from "../components/DataPrivacy";
import ShareSetup from "../components/ShareSetup";
import BankTemplates from "../components/BankTemplates";
import UpdateNotice from "../components/UpdateNotice";
import BackupTools, { restoredMsg, restoreNamed } from "../components/BackupTools";
import BookSettings from "../components/BookSettings";
import BookSwitch from "../components/BookSwitch";
import AppServer from "../components/AppServer";
import PlanSettings from "../components/Plan";
import MailAddress from "../components/MailAddress";
import DeleteAccount from "../components/DeleteAccount";
import DemoStats from "../components/DemoStats";
import Features from "../components/Features";
import { DocQuotaNote, useDocQuota } from "../components/DocQuota";
import { useBook, useDemo, useUiPrefs } from "../components/hooks";
import { LogoutButton } from "./More";

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
  MAIL_USER: <>Sadece banka maillerinin yönlendirildiği <b>ayrı</b> bir adres (ör. yeni bir Gmail). Ana mailini bağlama.</>,
  MAIL_PASSWORD: <>Gmail: Google Hesabı → Güvenlik → 2 adımlı doğrulama açık → <b>Uygulama şifreleri</b> → 16 haneli şifre. Normal şifren çalışmaz.</>,
  MAIL_IMAP_HOST: <>Gmail için değiştirme. Outlook: outlook.office365.com · Yandex: imap.yandex.com.tr</>,
  MAIL_FOLDER: <>Bankaların mailleri başka bir klasöre/etikete düşüyorsa adını yaz.</>,
  MAIL_SENDERS: <>Listede olmayan bir bankan varsa alan adını ekle (ör. ornekbank.com.tr).</>,
};
const MAIL_KEYS = ["MAIL_USER", "MAIL_PASSWORD", "MAIL_IMAP_HOST", "MAIL_FOLDER", "MAIL_SENDERS"];

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
  const testable = c.name === "GEMINI_API_KEY" || c.name === "TELEGRAM_BOT_TOKEN" || c.name === "MAIL_PASSWORD";

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

function CycleForm() {
  const ui = useUI();
  const meta = useMeta();
  const cur = meta.data?.cycle_start_day ?? 1;
  const auto = (meta.data?.cycle_mode ?? "auto") === "auto";
  const sug = meta.data?.suggested_cycle_day;
  const save = useSave((body: { mode: "auto" | "manual"; day?: number }) => fetch("/api/settings/cycle", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then((r) => r.json()), (r: { current_month: string; day: number }) => {
    ui.setMonth(r.current_month);
    ui.toast(r.day > 1 ? `Ay dönümü: her ayın ${r.day}. günü` : "Ay dönümü: takvim ayı");
  });
  const example = meta.data?.current_month ? periodRange(meta.data.current_month, cur) : null;
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><CalendarClock size={18} /> Ay dönümü</h2>
      <p className="text-sm text-stone-500">
        Bütçe ve özet hangi günden itibaren yeni aya geçsin? Kart kesim tarihine hizalı olunca ekstreyi ödeyince yeni dönem
        temiz başlar; elle girdiğin harcamalar da ilgili ekstreyle aynı döneme düşer.
      </p>
      <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
        <input type="checkbox" className="mt-0.5" checked={auto}
          onChange={(e) => save.mutate(e.target.checked ? { mode: "auto" } : { mode: "manual", day: cur })} />
        <span>
          <b>Otomatik</b> — her ekstre yüklemesinde kartların son kesim günlerine göre güncellenir
          {sug ? <span className="text-stone-500"> (şu an önerilen: {sug})</span> : <span className="text-stone-500"> (ekstre yükleyince)</span>}
        </span>
      </label>
      {!auto && (
        <select className="input w-auto" value={cur} aria-label="Ay dönümü günü"
          onChange={(e) => save.mutate({ mode: "manual", day: Number(e.target.value) })}>
          <option value={1}>Ayın 1'i (takvim ayı)</option>
          {Array.from({ length: 27 }, (_, i) => i + 2).map((d) => <option key={d} value={d}>Her ayın {d}. günü</option>)}
        </select>
      )}
      <p className="text-xs text-stone-500">
        Şu an: {cur > 1 ? `her ayın ${cur}. günü` : "takvim ayı"}
        {example && <> · {meta.data?.current_month} dönemi = {example}</>}
      </p>
    </section>
  );
}

function PdfPasswords() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["pdf-pw"], queryFn: () => fetch("/api/settings/pdf-passwords").then((r) => r.json() as Promise<{ index: number; hint: string }[]>) });
  const del = useSave((i: number) => fetch(`/api/settings/pdf-passwords/${i}`, { method: "DELETE" }), () => ui.toast("Şifre silindi"));
  if (!q.data?.length) return null;
  return (
    <section className="card p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Hatırlanan PDF şifreleri</h2>
      <p className="text-xs text-stone-500">Şifreli ekstreler yüklenirken sırayla denenir.</p>
      <ul className="mt-2 divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {q.data.map((p) => (
          <li key={p.index} className="flex items-center justify-between py-2">
            <code>{p.hint}</code>
            <button className="btn-danger px-2 py-1 text-xs" onClick={() => del.mutate(p.index)}>Sil</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PrivacyForm() {
  const [on, setOn] = useState(() => { try { return localStorage.getItem("hk-hide-on-open") !== "0"; } catch { return true; } });
  return (
    <section className="card p-4">
      <label className="flex cursor-pointer items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5" checked={on} onChange={(e) => {
          setOn(e.target.checked);
          try { localStorage.setItem("hk-hide-on-open", e.target.checked ? "1" : "0"); } catch { /* özel mod */ }
        }} />
        <span><b>Açılışta tutarları gizle</b> — uygulama her açıldığında tutarlar •••• görünür, göz ikonuyla açılır. Bu cihaz için geçerli.</span>
      </label>
    </section>
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
  telegram: { token: boolean; connected: boolean; chat_name: string | null };
  backup: { encrypted_offsite: boolean; files: { name: string; size: number; created: string }[]; last_run: string | null };
  last_reminder: string | null;
}

async function post<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "POST" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(detailText(j.detail, r.status));
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
  const book = useBook();
  const demo = useDemo();
  if (book.isLoading || demo.isLoading) return <></>;
  if (demo.data) return <DemoSettings />;
  const b = book.data;
  if (b?.member && b.book !== "kendi") return <MemberSettings owner={b.owner ?? ""} role={b.role} roleLabel={b.role_label} />;
  return <OwnerSettings ownBook={!!b?.member} />;
}

/** Kişisel görünüm: sade mod (büyük yazı), Özet düzenini sıfırla. */
function ViewPrefs() {
  const { ui, save } = useUiPrefs();
  return (
    <section className="card space-y-2 p-4">
      <h2 className="font-semibold">Görünüm</h2>
      <label className="flex cursor-pointer items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={ui.simple} onChange={(e) => save.mutate({ simple: e.target.checked })} />
        <span><b>Sade görünüm</b> — büyük yazı; Özet'te sadece "bu ay ne kadar harcadım", gelen, kalan ve yaklaşan ödemeler.
          <span className="block text-xs text-stone-500">Bu ayar sana özel; aynı defteri kullanan diğer kişiyi etkilemez.</span></span>
      </label>
      {ui.layout && (
        <button className="btn-ghost px-2 py-1 text-xs" onClick={() => save.mutate({ reset_layout: true })}>Özet düzenini varsayılana döndür</button>
      )}
    </section>
  );
}

/** Canlı demo: ayarlar kapalı; ne olduğunu ve nasıl kurulacağını anlat. */
function DemoSettings() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ayarlar</h1>
      <section className="card space-y-2 p-4 text-sm">
        <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Demoda ayarlar kapalı</h2>
        <p className="text-stone-600 dark:text-stone-400">
          Kendi sunucuna kurunca burada: yapay zekâ anahtarı (isteğe bağlı), Telegram bildirimleri, e-postadan otomatik ekstre,
          yedekler ve geri yükleme, ortak defter (eşinle), telefondan paylaş kısayolu, PDF şifreleri, ay dönümü günü.
        </p>
        <a className="btn-primary inline-flex" href="https://github.com/alameddinc/hesapkitap#-hızlı-kurulum" target="_blank" rel="noreferrer">
          Kendi sunucuna kur
        </a>
      </section>
    </div>
  );
}

/** Ortak defter üyesi: sadece kendi şifresi ve telefondan paylaşma. */
const ROLE_TEXT: Record<string, string> = {
  viewer: "Her şeyi görebilirsin; ekleme ve değiştirme defter sahibinde.",
  editor: "Fiş ve harcama ekleyebilir, kategori düzeltebilirsin; ekstre yükleme, silme ve ayarlar defter sahibinde.",
  full: "Ekleyebilir, yükleyebilir, düzenleyip silebilirsin; defter ayarları ve üyeler defter sahibinde.",
};

function MemberSettings({ owner, role, roleLabel }: { owner: string; role?: string; roleLabel?: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ayarlar</h1>
      <section className="card space-y-2 p-4 text-sm">
        <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Ortak defter</h2>
        <p className="text-stone-600 dark:text-stone-400">
          <b>{owner}</b> kullanıcısının defterini görüyorsun{roleLabel ? <> · yetkin: <b>{roleLabel}</b></> : null}. {ROLE_TEXT[role ?? "editor"]}
        </p>
        <MyBookNote />
      </section>
      <ViewPrefs />
      <ShareSetup />
      <PasswordForm />
      <LogoutButton />
    </div>
  );
}

/** Üye: kendi defterine geçiş (yalnız onun; sahip göremez). */
function MyBookNote() {
  return (
    <div className="space-y-1 border-t border-stone-100 pt-2 dark:border-stone-800">
      <p className="text-xs text-stone-500">Bir de <b>kendi defterin</b> var: sadece sen görürsün, orada her şeyi yapabilirsin.</p>
      <BookSwitch />
    </div>
  );
}

/** Sahip ya da üyenin kendi defteri (ownBook: ortak defter ayarı yok, geçiş var). */
function OwnerSettings({ ownBook }: { ownBook?: boolean }) {
  const ui = useUI();
  const st = useQuery({ queryKey: ["settings"], queryFn: () => fetch("/api/settings").then((r) => r.json() as Promise<SettingsStatus>) });
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const [pairInfo, setPairInfo] = useState<{ code: string; bot: string; link: string } | null>(null);
  const pair = useSave(() => post<{ code: string; bot: string; link: string }>("/api/settings/telegram/pair"), setPairInfo);
  const connect = useSave(() => post<{ name: string }>("/api/settings/telegram/connect"), (r) => {
    setPairInfo(null);
    ui.toast(`Bağlandı: ${r.name}`);
  });
  const test = useSave(() => post<{ sent: number }>("/api/settings/telegram/test"), (r) => ui.toast(`${r.sent} mesaj gönderildi`));
  const disconnect = useSave(() => post("/api/settings/telegram/disconnect"), () => ui.toast("Bağlantı kaldırıldı"));
  const report = useSave(() => post<{ month: string }>("/api/settings/telegram/report"), () => ui.toast("Rapor Telegram'a gönderildi"));
  const backupNow = useSave(() => post<{ path: string }>("/api/settings/backup/now"), (r) => ui.toast(`Yedek alındı: ${r.path}`));
  const backTo = useSave((name: string) => restoreNamed(name), (r) => ui.toast(restoredMsg(r)));
  const s = st.data;
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => fetch("/api/settings/config").then((r) => r.json() as Promise<ConfigItem[]>) });
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  const isAdmin = !auth.data?.user || auth.data.user.role === "admin";
  // barındırılan sürüm: anahtar/Telegram/e-posta kutusu ayarı yok; yapay zekâ paketin kotasıyla sunucudan
  const locked = !!useMeta().data?.plan?.hosted && !isAdmin;
  const ai = useDocQuota();

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Ayarlar</h1>
      {ownBook && (
        <section className="card space-y-2 p-4 text-sm">
          <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Benim defterim</h2>
          <p className="text-stone-600 dark:text-stone-400">Bu defter sadece senin; ortak defterin sahibi göremez. Ortak deftere buradan dönebilirsin.</p>
          <BookSwitch />
        </section>
      )}
      {isAdmin && <UpdateNotice />}

      <Features />

      <ViewPrefs />

      {locked ? <DocQuotaNote /> : (
      <section className="card p-4">
        <h2 className="flex items-center gap-2 font-semibold"><KeyRound size={18} /> Anahtarlar</h2>
        <p className="text-xs text-stone-500">Değerler bu sunucuda saklanır; ekranda sadece son 4 karakteri görünür.</p>
        {!isAdmin && ai.data && ai.data.used != null && (
          <p className="mt-2 rounded-lg bg-stone-50 p-2 text-sm dark:bg-stone-900">
            {ai.data.own_key
              ? <>Kendi Gemini anahtarını kullanıyorsun: okuma sınırı yok (bu ay {ai.data.used} okuma).</>
              : ai.data.shared_available
                ? <>Fiş/ekstre okuma: bu ay <b>{ai.data.used}/{ai.data.quota}</b> ücretsiz hak kullanıldı. Sınırsız için kendi anahtarını girebilirsin.</>
                : <>Bu sunucuda ortak anahtar yok; fiş okumak için kendi Gemini anahtarını gir.</>}
          </p>
        )}
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {cfg.data?.filter((c) => !MAIL_KEYS.includes(c.name)).map((c) => <KeyRow key={c.name} c={c} />)}
        </ul>
      </section>
      )}

      <ShareSetup />

      {!locked && <BankTemplates isAdmin={isAdmin} />}

      {/* hazır sürüm: deftere özel posta adresi (kendi sunucunda yerine IMAP e-posta kutusu) */}
      <MailAddress />

      {!locked && <MailSection keys={cfg.data?.filter((c) => MAIL_KEYS.includes(c.name)) ?? []} />}

      {auth.data?.user && !ownBook && <BookSettings />}

      {isAdmin && <Users />}

      {isAdmin && <DemoStats />}

      <AppServer />

      <PlanSettings />

      {!isAdmin && <DeleteAccount />}

      {isAdmin && <section className="card space-y-3 p-4">
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
              {s.telegram.connected ? (
                <span>
                  Bağlı: <b>{s.telegram.chat_name ?? "Telegram sohbeti"}</b> — hatırlatmalar sadece bu sohbete gider. Her ay
                  dönümünde biten dönemin raporu (gelir/gider, en çok harcananlar, artanlar, bütçe, abonelik) da gelir.
                  <span className="mt-1 block text-stone-500">
                    Kart ekstresi ya da hesap dökümü PDF'ini bu sohbete gönderirsen (mailden, WhatsApp'tan "Paylaş →
                    Telegram") bir dakika içinde içe aktarılır, sonucu bot yazar. Başka sohbetlerden gelenler işlenmez.
                  </span>
                </span>
              ) : "Telegram'ı bu hesaba bağla:"}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={!s.telegram.token || pair.isPending}
                  onClick={() => pair.mutate(undefined, onErr)}>
                  {s.telegram.connected ? "Başka sohbete bağla" : "Bağlantı kodu al"}
                </button>
                {pairInfo && (
                  <>
                    <a className="btn-primary" href={pairInfo.link} target="_blank" rel="noreferrer">Telegram'da aç</a>
                    <button className="btn-primary" disabled={connect.isPending} onClick={() => connect.mutate(undefined, onErr)}>
                      Başlat'a bastım, bağla
                    </button>
                  </>
                )}
              </div>
              {pairInfo && (
                <p className="mt-2 text-xs text-stone-500">
                  "Telegram'da aç" → @{pairInfo.bot} sohbetinde <b>BAŞLAT</b>'a bas (bota <code>/start {pairInfo.code}</code> gider).
                  Kod 10 dakika geçerli ve tek kullanımlık; bot adını bilen başka biri bağlanamaz.
                </p>
              )}
            </Step>
            <Step n={3} done={!!s.last_reminder}>
              <div className="flex flex-wrap gap-2">
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={!s.telegram.connected}
                  onClick={() => test.mutate(undefined, onErr)}><Send size={14} /> Test mesajı gönder</button>
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={!s.telegram.connected}
                  onClick={() => report.mutate(undefined, onErr)}>Geçen dönemin raporunu gönder</button>
                {s.telegram.connected && (
                  <button className="btn-danger" onClick={() => disconnect.mutate(undefined, onErr)}>Bağlantıyı kaldır</button>
                )}
              </div>
            </Step>
          </ol>
        )}
      </section>}

      <section className="card space-y-3 p-4">
        <h2 className="flex items-center gap-2 font-semibold"><DatabaseBackup size={18} /> Yedekler</h2>
        <p className="text-sm text-stone-500">
          Her gece 03:00'te otomatik; son 30 gün sunucuda tutulur.
          {s?.backup.encrypted_offsite && s.telegram.connected
            ? " Şifreli kopyası Telegram'a gönderiliyor."
            : " Sunucu dışı şifreli kopya için Telegram'ı bağla ve yukarıda yedek şifresi belirle."}
        </p>
        <BackupTools />
        <button className="btn-primary" disabled={backupNow.isPending} onClick={() => backupNow.mutate(undefined, onErr)}>
          Şimdi yedek al
        </button>
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {s?.backup.files.map((f) => (
            <li key={f.name} className="flex items-center justify-between gap-2 py-2">
              <span>{dateTR(f.created)} <span className="text-xs text-stone-500">· {(f.size / 1024).toFixed(0)} KB</span></span>
              <span className="flex gap-1">
                <button className="btn-ghost px-2 py-1 text-xs" disabled={backTo.isPending}
                  onClick={() => confirm(`${dateTR(f.created)} yedeğine dönülsün mü? Şu anki kayıtların yerini alır (önce şu anki hal yedeklenir).`)
                    && backTo.mutate(f.name, onErr)}>Bu yedeğe dön</button>
                <a className="btn-ghost px-2 py-1" href={`/api/settings/backup/${f.name}`} download><Download size={14} /> İndir</a>
              </span>
            </li>
          ))}
          {s && !s.backup.files.length && <li className="py-2 text-stone-500">Henüz yedek yok</li>}
        </ul>
      </section>

      <section className="card space-y-3 p-4">
        <h2 className="flex items-center gap-2 font-semibold"><Lock size={18} /> Verilerin nerede?</h2>
        <DataPrivacy mode={isAdmin ? "self" : "hosted"} />
      </section>
      <PrivacyForm />
      <CycleForm />
      <PdfPasswords />
      {auth.data?.password_source === "app" && <PasswordForm />}
    </div>
  );
}


interface MailLog {
  message_id: string;
  received_at: string;
  sender: string;
  subject: string;
  kind: string;
  status: "ok" | "skip" | "error";
  detail: string;
}

/** E-posta otomasyonu: bankaların mailleri (e-ekstre, işlem bildirimi, hesap özeti) kendiliğinden içe aktarılır. */
function MailSection({ keys }: { keys: ConfigItem[] }) {
  const ui = useUI();
  const st = useQuery({
    queryKey: ["mail"],
    queryFn: () => fetch("/api/mail/status").then((r) => r.json() as Promise<{ enabled: boolean; last_poll: string | null; poll_minutes: number; log: MailLog[] }>),
  });
  const poll = useSave(() => post<{ processed: number; ok: number; error: number }>("/api/mail/poll"),
    (r) => ui.toast(r.processed ? `${r.processed} yeni mail · ${r.ok} içe aktarıldı${r.error ? ` · ${r.error} hata` : ""}` : "Yeni mail yok"));
  const [showAll, setShowAll] = useState(false);
  const s = st.data;
  const log = (s?.log ?? []).filter((m) => showAll || m.status !== "skip");
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Mail size={18} /> E-posta otomasyonu</h2>
      <p className="text-sm text-stone-500">
        Bankaların gönderdiği <b>kart e-ekstreleri</b>, <b>işlem bildirimleri</b> (havale, maaş, fatura, kart harcaması) ve
        <b> hesap özetleri</b> {s?.poll_minutes ?? 10} dakikada bir okunup kendiliğinden içe aktarılır; Telegram'dan haber verilir.
        Sonradan gelen PDF aynı hareketi ikinci kez saymaz. Sadece banka alan adlarından gelen ve imzası doğrulanan mailler işlenir.
      </p>
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        <li>Sadece bu iş için yeni bir Gmail aç ve uygulama şifresi oluştur (aşağıdaki açıklamaya bak).</li>
        <li>Ana mailinde filtre kur: bankalardan gelenleri (ör. <code>from:(vakifbank.com.tr OR halkbank.com.tr OR kuveytturk.com.tr)</code>) bu adrese <b>otomatik ilet</b>.</li>
        <li>Bankaların uygulamasında <b>e-ekstre</b>yi ve <b>işlem bildirimlerini e-posta ile</b> aç.</li>
        <li>Şifreli PDF'ler için: aynı bankanın ekstresini bir kez Ekstreler'den yükleyip "şifreyi hatırla" de.</li>
      </ol>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {keys.map((c) => <KeyRow key={c.name} c={c} />)}
      </ul>
      {s?.enabled && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={poll.isPending}
              onClick={() => poll.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>
              <RefreshCw size={16} className={cx(poll.isPending && "animate-spin")} /> Şimdi kontrol et
            </button>
            <span className="text-xs text-stone-500">Son kontrol: {s.last_poll ? s.last_poll.replace("T", " ").slice(0, 16) : "henüz yok"}</span>
          </div>
          {s.log.length > 0 && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium">İşlenen mailler</span>
                <label className="flex items-center gap-1 text-xs text-stone-500">
                  <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> atlananları da göster
                </label>
              </div>
              <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
                {log.map((m) => (
                  <li key={m.message_id} className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className={cx("rounded px-1.5 py-0.5 text-xs",
                        m.status === "ok" ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                          : m.status === "error" ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                            : "bg-stone-100 text-stone-500 dark:bg-stone-800")}>
                        {m.status === "ok" ? "aktarıldı" : m.status === "error" ? "hata" : "atlandı"}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{m.subject || "(konusuz)"}</span>
                      <span className="text-xs text-stone-500">{dateTR(m.received_at)}</span>
                    </div>
                    <p className="mt-0.5 text-xs whitespace-pre-line text-stone-500">{maskText(m.detail)}</p>
                  </li>
                ))}
                {!log.length && <li className="px-3 py-2 text-xs text-stone-500">Henüz aktarılan mail yok.</li>}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
