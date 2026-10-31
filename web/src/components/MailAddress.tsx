import { useQuery } from "@tanstack/react-query";
import MailSubject from "./MailSubject";
import { ask } from "../lib/ask";
import { ChevronDown, Copy, Crown, Inbox, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type MailLogItem } from "../api";
import { cx, dateTR, maskText } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import { Upgrade, usePlan } from "./Plan";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const STATUS: Record<MailLogItem["status"], { icon: string; label: string; cls: string }> = {
  ok: { icon: "✓", label: "işlendi", cls: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  skip: { icon: "⏭", label: "atlandı", cls: "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400" },
  error: { icon: "⚠", label: "hata", cls: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
};

/** Ayarlar → Posta adresin (yalnız hazır sürüm; kendi sunucunda IMAP e-posta kutusu var). */
export default function MailAddress() {
  const plan = usePlan();
  const ui = useUI();
  const q = useQuery({ queryKey: ["mail-address"], queryFn: api.mailAddress, enabled: !!plan?.hosted, retry: false });
  const regen = useSave(api.regenerateMailAddress, () => ui.toast("Yeni adres hazır; eskisi artık çalışmıyor"));
  const onErr = { onError: (e: Error) => ui.toast(e.message) };

  if (!plan?.hosted || q.isError) return null; // giriş yoksa (409) gösterme
  const d = q.data;
  if (!d) return null;
  if (d.locked) {
    return (
      <section className="card space-y-2 p-6 text-center">
        <Crown size={32} className="mx-auto text-amber-500" />
        <h2 className="text-lg font-semibold">Posta adresin</h2>
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Deftere özel bir e-posta adresi: kart ekstreleri, hesap dökümleri, banka bildirimleri ve faturalar bu adrese
          gelince kendiliğinden işlenir.
        </p>
        <Upgrade need="Premium" />
      </section>
    );
  }

  const copy = () => navigator.clipboard.writeText(d.address).then(() => ui.toast("Adres kopyalandı"), () => ui.toast("Kopyalanamadı"));
  return (
    <section className="card space-y-4 p-4">
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Inbox size={18} /> Posta adresin</h2>
        <p className="text-sm text-stone-500">Bu adrese gelen belgeler kendiliğinden deftere işlenir. Adresi kimseyle paylaşma.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950/40">
        <code className="min-w-0 flex-1 text-base font-semibold break-all text-blue-800 select-all sm:text-lg dark:text-blue-300">{d.address}</code>
        <button className="btn-primary" onClick={copy}><Copy size={16} /> Kopyala</button>
      </div>
      <button className="btn-ghost px-2 py-1 text-xs" disabled={regen.isPending}
        onClick={() => void ask("Yeni adres oluşturulsun mu?\n\nEski adres hemen çalışmaz olur: oraya yönlendirdiğin mailler artık işlenmez, Gmail/Outlook yönlendirmesini yeni adresle güncellemen gerekir.").then((ok) => { if (ok) regen.mutate(undefined, onErr); })}>
        <RefreshCw size={14} className={cx(regen.isPending && "animate-spin")} /> Adresi yenile
      </button>

      <div className="text-sm">
        <div className="font-medium">Ne gönderebilirim?</div>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-stone-600 dark:text-stone-400">
          <li>Kart ekstresi PDF'i (e-ekstre)</li>
          <li>Hesap dökümü (hesap özeti)</li>
          <li>Banka bildirim mailleri (harcama, havale, maaş…)</li>
          <li>Faturalar: elektrik, su, doğalgaz, internet, telefon — Ödemeler'e "bu ay geldi" diye düşer</li>
        </ul>
      </div>

      <Guide address={d.address} />

      <Senders senders={d.senders} />

      <Log log={d.log} refreshing={q.isFetching} onRefresh={() => q.refetch()} />
    </section>
  );
}

function Guide({ address }: { address: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-stone-200 dark:border-stone-700">
      <button className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium" onClick={() => setOpen(!open)} aria-expanded={open}>
        Rehber: bankanın maillerini bu adrese otomatik ilet
        <ChevronDown size={16} className={cx("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-stone-100 px-3 py-3 text-sm dark:border-stone-800">
          <p className="text-stone-600 dark:text-stone-400">
            Otomatik iletme bankanın orijinal imzasını korur; bu yüzden en güvenilir yol budur. Önce bankanın uygulamasında
            <b> e-ekstre</b>yi ve <b>işlem bildirimlerini e-posta ile</b> aç.
          </p>
          <div>
            <div className="font-medium">Gmail</div>
            <ol className="mt-1 list-decimal space-y-1 pl-5">
              <li>Bilgisayardan Gmail → sağ üstte ⚙ → <b>Tüm ayarları görün</b> → <b>Yönlendirme ve POP/IMAP</b>.</li>
              <li><b>Yönlendirme adresi ekle</b> → <code className="break-all">{address}</code> → İleri → Devam.</li>
              <li>
                Gmail bu adrese bir onay maili yollar. Birkaç saniye sonra aşağıdaki <b>Son gelenler</b> listesine "atlandı"
                olarak düşer; <b>onay kodu konu satırında</b> görünür (ör. "(#123456789) Gmail Yönlendirme Onayı"). Gmail'deki
                onay bağlantısını açamazsın; kodu Gmail ayarlarındaki <b>Doğrula</b> kutusuna yaz.
              </li>
              <li>
                "Gelen postanın bir kopyasını yönlendir"i <b>açma</b> — bütün mailin gelmesin. Onun yerine filtre kur:
                <b> Filtreler ve engellenen adresler</b> → <b>Yeni filtre oluştur</b> → <b>Kimden</b>:
                <code className="mt-1 block rounded bg-stone-100 p-1.5 text-xs break-all dark:bg-stone-800">vakifbank.com.tr OR garantibbva.com.tr OR enerjisa.com.tr OR turktelekom.com.tr</code>
                (kendi bankaların ve fatura kurumların) → <b>Filtre oluştur</b> → <b>Şu adrese yönlendir</b>: bu adres → Filtre oluştur.
              </li>
            </ol>
          </div>
          <div>
            <div className="font-medium">Outlook / Hotmail</div>
            <p className="mt-1 text-stone-600 dark:text-stone-400">
              Ayarlar → Posta → <b>Kurallar</b> → Yeni kural ekle → Koşul: <b>Kimden</b> (bankanın / kurumun adresi) →
              Eylem: <b>Yönlendir</b> (redirect) → bu adres → Kaydet. Yönlendirme bankanın orijinal mailini olduğu gibi
              gönderir. "İlet" (forward) seçersen mail senin adresinden gider; o zaman Outlook adresini aşağıdaki
              <b> Güvendiğin adresler</b>e ekle.
            </p>
          </div>
          <p className="text-xs text-stone-500">
            Elle de iletebilirsin: aşağıdaki <b>Güvendiğin adresler</b>e kendi e-postanı ekle, belgeyi o adresten bu adrese gönder.
          </p>
        </div>
      )}
    </div>
  );
}

function Senders({ senders }: { senders: string[] }) {
  const ui = useUI();
  const [value, setValue] = useState("");
  const save = useSave(api.setMailSenders);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const v = value.trim().toLowerCase();
  const valid = EMAIL.test(v) && !senders.includes(v);
  const add = () => valid && save.mutate([...senders, v], { ...onErr, onSuccess: () => { setValue(""); ui.toast("Eklendi"); } });
  return (
    <div className="space-y-2 text-sm">
      <div>
        <div className="font-medium">Güvendiğin adresler</div>
        <p className="text-xs text-stone-500">
          Bu adreslerden elle ilettiğin ekstre, döküm ve faturalar kabul edilir. İmza yine kontrol edilir: başkası senin adına
          mail atamaz. En çok 10 adres.
        </p>
      </div>
      {senders.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
          {senders.map((s) => (
            <li key={s} className="flex items-center justify-between gap-2 px-3 py-1.5">
              <span className="min-w-0 truncate">{s}</span>
              <button className="btn-ghost p-1 text-red-600" aria-label={`${s} adresini sil`} disabled={save.isPending}
                onClick={() => void ask(`${s} güvenilen adreslerden çıkarılsın mı?\n\nBu adresten iletilen belgeler artık kabul edilmez.`, { confirm: "Çıkar", irreversible: false })
                  .then((ok) => { if (ok) save.mutate(senders.filter((x) => x !== s), onErr); })}><Trash2 size={14} /></button>
            </li>
          ))}
        </ul>
      )}
      {senders.length < 10 && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <input className="input min-w-0 flex-1" type="email" inputMode="email" autoComplete="email" placeholder="ornek@gmail.com"
            value={value} onChange={(e) => setValue(e.target.value)} aria-label="Güvendiğin e-posta adresi" />
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" type="submit" disabled={!valid || save.isPending}>
            <Plus size={14} /> Ekle</button>
        </form>
      )}
    </div>
  );
}

function Log({ log, refreshing, onRefresh }: { log: MailLogItem[]; refreshing: boolean; onRefresh: () => void }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Son gelenler</span>
        <button className="btn-ghost px-2 py-1 text-xs" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw size={13} className={cx(refreshing && "animate-spin")} /> Yenile</button>
      </div>
      <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
        {log.map((m) => {
          const st = STATUS[m.status] ?? STATUS.skip;
          return (
            <li key={m.message_id} className="px-3 py-2">
              <div className="flex items-start gap-2">
                <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-xs", st.cls)} title={st.label}>{st.icon} {st.label}</span>
                {/* konu her zaman görünür: Gmail yönlendirme onay kodu buradadır */}
                <MailSubject subject={m.subject} className="min-w-0 flex-1 font-medium" />
                <span className="shrink-0 text-xs text-stone-500">{dateTR(m.received_at)}</span>
              </div>
              <div className="mt-0.5 truncate text-xs text-stone-500">{m.sender || "gönderen bilinmiyor"}</div>
              {m.detail && <p className="mt-0.5 text-xs whitespace-pre-line text-stone-500">{maskText(m.detail)}</p>}
            </li>
          );
        })}
        {!log.length && <li className="px-3 py-2 text-xs text-stone-500">Henüz gelen mail yok.</li>}
      </ul>
    </div>
  );
}
