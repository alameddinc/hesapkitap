// Yönetici: "Sorunlar" (sunucu app/incidents.py, yetenek "incidents"; mobil components/hesap/Sorunlar.tsx'in eşi).
// Kullanıcıların karşılaştığı hatalar türe göre gruplu, KİŞİSEL VERİ OLMADAN: kaç kişi, kaç kez, ilk/son görülme, sürüm ve
// platform dağılımı, örnek bağlam. Türe tıklayınca ayrıntı: günlük çubuklar, kişiler (tıklayınca kişinin zaman çizelgesi),
// durum (yeni / inceleniyor / düzeltildi), not, düzeltme sürümü, kullanıcıya gidecek "düzelttik" metni ve haber anahtarı.
//   GET   /api/admin/incidents?status&severity&days → { items, counts, days, thresholds }
//   GET   /api/admin/incidents/{id}?days          → tür + { days: [{day, count, users}], people: [...] }
//   GET   /api/admin/incidents/users/{uid}?days   → { user, items }
//   PATCH /api/admin/incidents/{id} {status, note, fixed_version, user_text, notify_users} → { kind, notified }
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cx, dateTimeTR, parseTs } from "../format";
import { call } from "../lib/apiError";
import { useUI } from "../ui";
import Modal from "./Modal";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

export type SorunDurum = "yeni" | "inceleniyor" | "duzeltildi";
export type SorunAgirlik = "kritik" | "uyari" | "bilgi";
export interface SorunTuru {
  id: number; code: string; label: string; severity: SorunAgirlik; source: "sunucu" | "istemci" | "is"; place: string;
  status: SorunDurum; note: string | null; fixed_version: string | null; fixed_at: string | null; user_text: string | null;
  notified: number; notified_at: string | null; reopened: number; first_seen: string; last_seen: string;
  sample: { ozet?: string; http?: number; platform?: string; app_version?: string };
  notifiable: boolean; notice: string | null;
  users: number; count: number; anon?: number; last_hour_users?: number;
  versions?: { key: string; n: number }[]; platforms?: { key: string; n: number }[];
}
interface SorunKisi { user_id: number | null; username: string | null; count: number; first_at: string; last_at: string; app_version: string | null; platform: string | null }
interface SorunAyrinti extends SorunTuru { days: { day: string; count: number; users: number }[]; people: SorunKisi[] }
interface SorunListe {
  items: SorunTuru[]; counts: Record<SorunDurum, number>; days: number;
  thresholds: { spike_users: number; spike_minutes: number; alert_gap_minutes: number; keep_days: number; notify_days: number };
}
interface KisiCizelge {
  user: { id: number; username: string | null };
  items: { kind_id: number; code: string; label: string; place: string; status: SorunDurum; day: string; count: number;
    first_at: string; last_at: string; app_version: string | null; platform: string | null }[];
}

export const INCIDENTS_KEY = ["admin-incidents"];
const DURUM: Record<SorunDurum, string> = { yeni: "Yeni", inceleniyor: "İnceleniyor", duzeltildi: "Düzeltildi" };
const AGIRLIK: Record<SorunAgirlik, string> = { kritik: "Kritik", uyari: "Uyarı", bilgi: "Bilgi" };
const KAYNAK: Record<SorunTuru["source"], string> = { sunucu: "Sunucu", istemci: "Uygulama", is: "Arka plan" };
const PLATFORM: Record<string, string> = { ios: "iPhone", android: "Android", web: "Web", bilinmiyor: "?" };
const DURUM_TON: Record<SorunDurum, string> = {
  yeni: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  inceleniyor: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  duzeltildi: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
};
const AGIRLIK_TON: Record<SorunAgirlik, string> = {
  kritik: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  uyari: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  bilgi: "bg-stone-200 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
};
const zaman = (s: string | null | undefined) => (s ? dateTimeTR(parseTs(s)) : "");
const Rozet = ({ text, tone }: { text: string; tone: string }) =>
  <span className={cx("shrink-0 rounded-md px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap", tone)}>{text}</span>;
const Cip = ({ children }: { children: React.ReactNode }) =>
  <span className="rounded-md bg-stone-100 px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-stone-600 dark:bg-stone-800 dark:text-stone-300">{children}</span>;

/** Yeni sorun sayısı (Genel kutusu ve sekme rozeti). */
export const useIncidentCounts = (on: boolean) => useQuery({
  queryKey: [...INCIDENTS_KEY, "counts"], retry: false, enabled: on,
  queryFn: () => call<SorunListe>("GET", "/api/admin/incidents?days=30"), select: (d) => d.counts,
});

function Kart({ it, onAc }: { it: SorunTuru; onAc: () => void }) {
  return (
    <li>
      <button type="button" onClick={onAc} data-testid="sorun-turu"
        className="w-full space-y-1.5 px-3 py-3 text-left hover:bg-stone-50 sm:px-4 dark:hover:bg-stone-800/50">
        <span className="flex items-start gap-2">
          <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{it.label}</span>
          <Rozet text={AGIRLIK[it.severity]} tone={AGIRLIK_TON[it.severity]} />
          <Rozet text={DURUM[it.status]} tone={DURUM_TON[it.status]} />
          <ChevronRight size={16} className="mt-0.5 shrink-0 text-stone-400" aria-hidden />
        </span>
        <span className="block font-mono text-xs text-stone-500 [overflow-wrap:anywhere] dark:text-stone-400">{KAYNAK[it.source]} · {it.place}</span>
        <span className="block text-sm">
          <b className="tabular-nums">{it.users}</b> kişi · <b className="tabular-nums">{it.count}</b> kez{it.anon ? ` (${it.anon} oturumsuz)` : ""}
          {it.last_hour_users ? <span className="text-red-600 dark:text-red-400"> · son 1 saatte {it.last_hour_users} kişi</span> : null}
        </span>
        <span className="block text-xs text-stone-500 dark:text-stone-400">İlk {zaman(it.first_seen)} · Son {zaman(it.last_seen)}{it.reopened ? ` · ${it.reopened} kez yeniden açıldı` : ""}</span>
        {(!!it.versions?.length || !!it.platforms?.length) && (
          <span className="flex flex-wrap gap-1">
            {it.versions?.map((v) => <Cip key={v.key}>{v.key === "bilinmiyor" ? "sürüm ?" : v.key} · {v.n}</Cip>)}
            {it.platforms?.map((p) => <Cip key={`p-${p.key}`}>{PLATFORM[p.key] ?? p.key} · {p.n}</Cip>)}
          </span>
        )}
        {it.sample?.ozet && <span className="block rounded-md bg-stone-100 px-2 py-1 text-xs text-stone-600 [overflow-wrap:anywhere] dark:bg-stone-800 dark:text-stone-300">{it.sample.ozet}</span>}
        {it.note && <span className="block text-xs text-stone-500 dark:text-stone-400">Not: {it.note}</span>}
      </button>
    </li>
  );
}

function KisiCizelgesi({ uid, ad, onClose }: { uid: number; ad: string; onClose: () => void }) {
  const q = useQuery({ queryKey: [...INCIDENTS_KEY, "user", uid], retry: false, queryFn: () => call<KisiCizelge>("GET", `/api/admin/incidents/users/${uid}?days=30`) });
  return (
    <Modal title={ad} onClose={onClose}>
      <p className="text-xs text-stone-500">Son 30 günde bu kişinin karşılaştığı sorunlar (en yeni üstte).</p>
      <SorguDurumu q={q} etiket="Zaman çizelgesi yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
      {q.data && !q.data.items.length && <p className="py-4 text-center text-sm text-stone-500">Kayıt yok.</p>}
      {!!q.data?.items.length && (
        <ol className="mt-2 divide-y divide-stone-100 dark:divide-stone-800">
          {q.data.items.map((x, i) => (
            <li key={i} className="space-y-0.5 py-2">
              <span className="flex items-start gap-2"><b className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">{x.label}</b><Rozet text={DURUM[x.status]} tone={DURUM_TON[x.status]} /></span>
              <span className="block font-mono text-xs text-stone-500 [overflow-wrap:anywhere]">{x.place}</span>
              <span className="block text-xs text-stone-500">{zaman(x.last_at)} · {x.count} kez{x.app_version ? ` · ${x.app_version}` : ""}{x.platform ? ` · ${PLATFORM[x.platform] ?? x.platform}` : ""}</span>
            </li>
          ))}
        </ol>
      )}
    </Modal>
  );
}

function Ayrinti({ id, onClose }: { id: number; onClose: () => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...INCIDENTS_KEY, "kind", id], retry: false, queryFn: () => call<SorunAyrinti>("GET", `/api/admin/incidents/${id}?days=30`) });
  const [form, setForm] = useState<{ status?: SorunDurum; note?: string; fixed_version?: string; user_text?: string; notify?: boolean }>({});
  const [busy, setBusy] = useState(false);
  const [kisi, setKisi] = useState<{ id: number; ad: string } | null>(null);
  const d = q.data;
  const status = form.status ?? d?.status ?? "yeni";
  const notify = form.notify ?? true;
  const fixing = !!d && status === "duzeltildi" && d.status !== "duzeltildi";
  const kisiler = d?.people.filter((u) => u.user_id).length ?? 0;
  const metin = (form.user_text ?? d?.user_text ?? "").trim();
  const onizleme = d && (metin ? `${metin.replace(/[.\s]+$/, "")}.` : d.notice);
  const dirty = Object.keys(form).length > 0;

  async function kaydet() {
    if (!d || busy) return;
    setBusy(true);
    try {
      const r = await call<{ kind: SorunAyrinti; notified: number | null }>("PATCH", `/api/admin/incidents/${id}`, {
        ...(form.status && form.status !== d.status ? { status: form.status } : {}),
        ...(form.note !== undefined ? { note: form.note } : {}),
        ...(form.fixed_version !== undefined ? { fixed_version: form.fixed_version } : {}),
        ...(form.user_text !== undefined ? { user_text: form.user_text } : {}),
        notify_users: notify,
      });
      setForm({});
      qc.setQueryData([...INCIDENTS_KEY, "kind", id], r.kind);
      void qc.invalidateQueries({ queryKey: INCIDENTS_KEY });
      ui.toast(r.notified != null ? `Kaydedildi · ${r.notified} kişiye haber gitti` : "Kaydedildi");
    } catch (e) {
      ui.error(e, { yedek: "Kaydedilemedi" });
    } finally {
      setBusy(false);
    }
  }

  const max = Math.max(1, ...(d?.days.map((x) => x.count) ?? [1]));
  return (
    <>
      <Modal title={d?.label ?? "Sorun"} onClose={onClose} dirty={dirty} onSave={kaydet} hidden={!!kisi}
        footer={<><div className="flex-1" /><button type="button" className="btn-ghost" onClick={onClose}>Kapat</button>
          <button type="button" className="btn-primary" disabled={!dirty || busy} onClick={() => void kaydet()}>{busy ? "Kaydediliyor…" : "Kaydet"}</button></>}>
        <SorguDurumu q={q} etiket="Sorun yükleniyor"><SatirIskelet n={4} /></SorguDurumu>
        {d && (
          <div className="space-y-4">
            <div className="space-y-1.5 rounded-xl bg-stone-50 p-3 dark:bg-stone-800/60">
              <span className="flex flex-wrap gap-1.5">
                <Rozet text={AGIRLIK[d.severity]} tone={AGIRLIK_TON[d.severity]} />
                <Rozet text={KAYNAK[d.source]} tone="bg-stone-200 text-stone-700 dark:bg-stone-700 dark:text-stone-200" />
                <Cip>{d.code}</Cip>
              </span>
              <span className="block font-mono text-xs text-stone-500 [overflow-wrap:anywhere] dark:text-stone-400">{d.place}</span>
              <span className="block text-sm"><b>{d.users}</b> kişi · <b>{d.count}</b> kez (son 30 gün)</span>
              <span className="block text-xs text-stone-500 dark:text-stone-400">İlk {zaman(d.first_seen)} · Son {zaman(d.last_seen)}</span>
              {d.sample?.ozet && <span className="block rounded-md bg-white px-2 py-1 text-xs text-stone-600 [overflow-wrap:anywhere] dark:bg-stone-900 dark:text-stone-300">{d.sample.ozet}{d.sample.http ? ` · HTTP ${d.sample.http}` : ""}</span>}
              {!!d.days.length && (
                <div className="flex h-16 items-end gap-0.5 pt-2" role="img" aria-label={`Günlük: ${d.days.map((x) => `${x.day} ${x.count}`).join(", ")}`}>
                  {d.days.map((x) => <span key={x.day} title={`${x.day}: ${x.count}`} className="min-w-1 flex-1 rounded-t bg-blue-500 dark:bg-blue-400" style={{ height: `${Math.max(6, (x.count / max) * 100)}%` }} />)}
                </div>
              )}
            </div>

            <fieldset>
              <legend className="label">Durum</legend>
              <div className="inline-flex flex-wrap gap-1 rounded-xl bg-stone-100 p-1 dark:bg-stone-800" role="radiogroup" aria-label="Durum">
                {(["yeni", "inceleniyor", "duzeltildi"] as const).map((s) => (
                  <button key={s} type="button" role="radio" aria-checked={status === s} onClick={() => setForm((f) => ({ ...f, status: s }))}
                    className={cx("rounded-lg px-3 py-1.5 text-sm", status === s ? "bg-white font-semibold shadow-sm dark:bg-stone-900" : "text-stone-600 dark:text-stone-300")}>{DURUM[s]}</button>
                ))}
              </div>
            </fieldset>
            <label className="block">
              <span className="label">Not (sürüm, commit)</span>
              <textarea className="input" rows={2} maxLength={2000} value={form.note ?? d.note ?? ""} placeholder="ör. 3325025 ile düzeldi"
                onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
            </label>
            <label className="block">
              <span className="label">Düzeltmenin geldiği uygulama sürümü</span>
              <input className="input" maxLength={20} value={form.fixed_version ?? d.fixed_version ?? ""} placeholder="ör. 1.0.9 (boş: sunucu düzeltmesi)"
                onChange={(e) => setForm((f) => ({ ...f, fixed_version: e.target.value }))} />
              <span className="mt-1 block text-xs text-stone-500">Altındaki sürümü kullananlara bildirimde "Önce uygulamayı güncelle." eklenir.</span>
            </label>
            {d.notifiable || metin ? (
              <label className="block">
                <span className="label">Kullanıcıya gidecek metin</span>
                <input className="input" maxLength={160} value={form.user_text ?? d.user_text ?? ""} placeholder={d.notice ?? "ör. Fiş okuma sorununu düzelttik"}
                  onChange={(e) => setForm((f) => ({ ...f, user_text: e.target.value }))} />
                {onizleme && <span className="mt-1.5 block rounded-md bg-stone-100 px-2 py-1 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-300">Bildirim: <b>Sorun düzeltildi</b> — {onizleme}</span>}
              </label>
            ) : (
              <p className="text-xs text-stone-500">Bu tür kullanıcıya "düzelttik" bildirimi göndermez (kota, sınır, arka plan işi). İstersen metin yazarak gönderebilirsin.</p>
            )}
            {fixing && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={notify} onChange={() => setForm((f) => ({ ...f, notify: !notify }))} />
                Düzeltildi deyince {kisiler} kişiye bir kez haber ver
              </label>
            )}
            {d.notified > 0 && <p className="text-xs text-stone-500">Daha önce {d.notified} kişiye "düzelttik" bildirimi gitti{d.notified_at ? ` (${zaman(d.notified_at)})` : ""}.</p>}

            <section>
              <h3 className="label">Kişiler ({d.people.length})</h3>
              <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
                {d.people.map((u, i) => (
                  <li key={u.user_id ?? `anon-${i}`}>
                    <button type="button" disabled={!u.user_id} onClick={() => u.user_id && setKisi({ id: u.user_id, ad: u.username ?? `#${u.user_id}` })}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left enabled:hover:bg-stone-50 dark:enabled:hover:bg-stone-800/50">
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium [overflow-wrap:anywhere]">{u.user_id ? u.username ?? `Silinmiş hesap #${u.user_id}` : "Oturumsuz (giriş ekranı, demo)"}</span>
                        <span className="block text-xs text-stone-500">{u.count} kez · son {zaman(u.last_at)}{u.app_version ? ` · ${u.app_version}` : ""}{u.platform ? ` · ${PLATFORM[u.platform] ?? u.platform}` : ""}</span>
                      </span>
                      {u.user_id ? <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden /> : null}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </Modal>
      {kisi && <KisiCizelgesi uid={kisi.id} ad={kisi.ad} onClose={() => setKisi(null)} />}
    </>
  );
}

export default function AdminIncidents() {
  const [status, setStatus] = useState<SorunDurum | "hepsi">("yeni");
  const [bilgi, setBilgi] = useState(false);
  const [acik, setAcik] = useState<number | null>(null);
  const q = useQuery({ queryKey: [...INCIDENTS_KEY, "list", status], retry: false,
    queryFn: () => call<SorunListe>("GET", `/api/admin/incidents?days=30${status === "hepsi" ? "" : `&status=${status}`}`) });
  const counts = q.data?.counts;
  const items = (q.data?.items ?? []).filter((x) => bilgi || x.severity !== "bilgi");
  const gizli = (q.data?.items.length ?? 0) - items.length;
  const t = q.data?.thresholds;
  return (
    <section className="card space-y-3 p-4" aria-labelledby="sr-h">
      <h2 id="sr-h" className="flex items-center gap-2 font-semibold"><AlertOctagon size={18} aria-hidden /> Sorunlar</h2>
      <p className="text-xs text-stone-500">Kullanıcıların karşılaştığı hatalar, türe göre gruplu (son 30 gün). İçerik, tutar, isim, dosya kaydedilmez.</p>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Durum">
        {([["yeni", "Yeni"], ["inceleniyor", "İnceleniyor"], ["duzeltildi", "Düzeltildi"], ["hepsi", "Hepsi"]] as const).map(([v, l]) => (
          <button key={v} type="button" role="radio" aria-checked={status === v} onClick={() => setStatus(v)}
            className={cx("rounded-full border px-3 py-1 text-sm", status === v ? "border-blue-600 bg-blue-600 text-white" : "border-stone-300 dark:border-stone-700")}>
            {l}{v !== "hepsi" && counts ? ` ${counts[v]}` : ""}
          </button>
        ))}
      </div>
      <SorguDurumu q={q} etiket="Sorunlar yükleniyor"><SatirIskelet n={3} /></SorguDurumu>
      {q.data && !items.length && <p className="py-6 text-center text-sm text-stone-500">{status === "yeni" ? "Yeni sorun yok." : "Bu durumda sorun yok."}</p>}
      {!!items.length && (
        <ul className="-mx-4 divide-y divide-stone-100 border-y border-stone-100 dark:divide-stone-800 dark:border-stone-800">
          {items.map((it) => <Kart key={it.id} it={it} onAc={() => setAcik(it.id)} />)}
        </ul>
      )}
      {(gizli > 0 || bilgi) && (
        <button type="button" className="text-sm font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setBilgi((b) => !b)}>
          {bilgi ? "Bilgi türlerini gizle" : `Bilgi türlerini de göster (${gizli})`}</button>
      )}
      {t && (
        <p className="text-xs text-stone-500">
          Yeni kritik tür ya da son {t.spike_minutes} dakikada {t.spike_users}+ kişi → telefonuna bildirim ve Telegram (tür başına {t.alert_gap_minutes} dakikada
          en çok bir). Kayıtlar {t.keep_days} gün saklanır.</p>
      )}
      {acik != null && <Ayrinti id={acik} onClose={() => setAcik(null)} />}
    </section>
  );
}
