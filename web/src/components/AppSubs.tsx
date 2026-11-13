import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, Camera, ExternalLink, Plus, Search, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { call, send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useCap, useSave } from "./hooks";
import { SubPaymentsFor } from "./SubDetail";
import { shrink } from "./AddSheet";
import Modal from "./Modal";
import Segmented from "./Segmented";

// Uygulama mağazası abonelikleri (sunucu app/store_subs.py, yetenek store_subscriptions): App Store / Google Play'in
// genel ekstre satırlarından ("APPLE.COM/BILL", "GOOGLE *YouTube") ayrı ayrı abonelik adayları. Adsız aday da tam
// abonelik: "Apple aboneliği · ₺59,99 · her ay 14'ü", sonraki tahsilat, yönet bağlantısı. Ad: katalogdan seç, fiyattan
// "Olabilir" önerisi ya da abonelik listesinin ekran görüntüsü. Tutarlar tl() ile (göz kapalıysa ••••).

export interface SubLogo { id: string | null; name: string | null; glyph: string; color: string; fg: string; group: string }
interface Guess extends SubLogo { plan: string; price: number; cycle: string }
interface Manage { store: "apple" | "google"; label: string; url: string; app_url: string | null }
export interface AppSub {
  id: string; store: "apple" | "google" | "diger" | null; store_label: string; name: string | null; title: string;
  catalog_id: string | null; named_by: string | null; logo: SubLogo; manage: Manage[];
  status: "abonelik" | "aday" | "degil" | "iptal"; confirmed: boolean; unknown: boolean;
  cycle: "aylik" | "yillik" | null; day: number | null; cycle_text: string; amount: number | null; monthly: number | null;
  yearly: number | null; counted: boolean; prev_amount: number | null; price_up: { from: number; to: number; date: string } | null;
  first_text: string; next_date: string | null; next_text: string | null; stale: boolean; stale_text: string | null;
  cancelled_at: string | null; charged_after_cancel: number; guesses: Guess[]; source: string | null; source_id: number | null;
  plan: string | null; charges: { date: string; amount: number; via: "kart" | "hesap"; uid: string }[]; pending: boolean;
}
interface Help {
  title: string; text: string; steps: { apple: string[]; google: string[] };
  open: Record<"apple" | "google", { label: string; url: string; app_url: string | null }>;
  mail: { title: string; text: string; filter: string; available: boolean };
}
export interface AppsData {
  available: boolean; items: AppSub[]; other: AppSub[]; candidates: number; help: Help;
  totals: { monthly: number; yearly: number; count: number; text: string | null };
}
interface CatalogItem extends SubLogo { id: string; name: string; stores: string[] }
interface Catalog { items: CatalogItem[]; groups: Record<string, string> }

const STORE_NAMES = { apple: "App Store", google: "Google Play" } as const;

export function SubLogoBox({ logo, size = 36 }: { logo: SubLogo; size?: number }) {
  return (
    <span aria-hidden="true" className="flex shrink-0 items-center justify-center rounded-lg font-bold"
      style={{ width: size, height: size, background: logo.color, color: logo.fg, fontSize: logo.glyph.length > 2 ? size * 0.3 : size * 0.4 }}>
      {logo.glyph}
    </span>
  );
}

const amountLine = (s: AppSub) => (s.amount != null ? `${tl(s.amount)} · ${s.cycle_text}` : s.cycle_text);

function Badges({ s }: { s: AppSub }) {
  const b: [string, string][] = [];
  if (s.pending) b.push(["Henüz ekstrede yok", "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"]);
  if (s.status === "aday") b.push(["Tek çekim · abonelik mi?", "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"]);
  if (s.status === "iptal") b.push(["İptal ettim", "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300"]);
  if (s.status === "degil") b.push(["Abonelik değil", "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300"]);
  if (s.stale) b.push(["Durmuş olabilir", "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300"]);
  if (s.price_up) b.push(["Zam", "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"]);
  if (!b.length) return null;
  return <span className="flex flex-wrap gap-1">{b.map(([t, c]) => <span key={t} className={cx("rounded-full px-2 py-0.5 text-[11px] font-medium", c)}>{t}</span>)}</span>;
}

function Row({ s, onOpen }: { s: AppSub; onOpen: () => void }) {
  const dim = s.status === "degil" || (s.status === "iptal" && !s.charged_after_cancel) || s.stale;
  return (
    <li>
      <button type="button" onClick={onOpen} className={cx("flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50", dim && "opacity-60")}
        aria-label={`${s.title}: ayrıntı`}>
        <SubLogoBox logo={s.logo} />
        <span className="min-w-0 flex-1">
          <span className={cx("block font-medium", s.status === "iptal" && "line-through")}>{s.title}</span>
          <span className="block text-xs text-stone-500">{s.cycle_text}{s.next_text ? ` · ${s.next_text}` : ""}</span>
          <span className="mt-1 block"><Badges s={s} /></span>
          {s.charged_after_cancel > 0 && (
            <span className="mt-1 flex items-center gap-1 text-xs font-medium text-red-600"><AlertOctagon size={13} /> İptalden sonra {tl(s.charged_after_cancel)} çekildi</span>
          )}
          {!s.name && s.guesses.length > 0 && (
            <span className="mt-1 block text-xs text-stone-500">Olabilir: {s.guesses.map((g) => g.plan).join(", ")}</span>
          )}
        </span>
        <span className="shrink-0 text-right text-sm font-semibold tabular-nums">
          {s.amount != null ? tl(s.amount) : "—"}
          <span className="block text-xs font-normal text-stone-500">{s.cycle === "yillik" ? "/yıl" : s.cycle === "aylik" ? "/ay" : ""}</span>
        </span>
      </button>
    </li>
  );
}

function useCatalog() {
  return useQuery({ queryKey: ["sub-catalog"], queryFn: () => send<Catalog>("/api/subscriptions/catalog"), staleTime: 3600_000 });
}

/** Katalogdan seç (arama + gruplar) ya da adını yaz. */
function CatalogPicker({ title, onPick, onClose, allowCustom = true }: {
  title: string; onPick: (v: { catalog_id?: string; name?: string }) => void; onClose: () => void; allowCustom?: boolean;
}) {
  const cat = useCatalog();
  const [q, setQ] = useState("");
  const [custom, setCustom] = useState("");
  const list = useMemo(() => {
    const k = q.trim().toLocaleLowerCase("tr");
    return (cat.data?.items ?? []).filter((x) => !k || x.name.toLocaleLowerCase("tr").includes(k));
  }, [cat.data, q]);
  const groups = cat.data?.groups ?? {};
  const byGroup = Object.keys(groups).map((g) => [g, list.filter((x) => x.group === g)] as const).filter(([, xs]) => xs.length);
  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-3">
        <label className="relative block">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400" aria-hidden="true" />
          <input className="input pl-9" placeholder="Ara: Netflix, iCloud, Exxen…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Katalogda ara" autoFocus />
        </label>
        {cat.isLoading && <p className="text-sm text-stone-500">Katalog yükleniyor…</p>}
        {byGroup.map(([g, xs]) => (
          <div key={g}>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">{groups[g]}</div>
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
              {xs.map((x) => (
                <button key={x.id!} type="button" onClick={() => onPick({ catalog_id: x.id! })}
                  className="flex min-h-11 items-center gap-2 rounded-lg border border-stone-200 px-2 py-1.5 text-left text-sm hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800">
                  <SubLogoBox logo={x} size={28} /><span className="min-w-0 truncate">{x.name}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
        {cat.data && !list.length && <p className="text-sm text-stone-500">Katalogda yok; adını aşağıya yaz.</p>}
        {allowCustom && (
          <div className="flex gap-2 border-t border-stone-100 pt-3 dark:border-stone-800">
            <input className="input flex-1" placeholder="Başka bir ad yaz" value={custom} maxLength={60} onChange={(e) => setCustom(e.target.value)} aria-label="Abonelik adı" />
            <button className="btn-primary shrink-0" disabled={!custom.trim()} onClick={() => onPick({ name: custom.trim() })}>Kaydet</button>
          </div>
        )}
      </div>
    </Modal>
  );
}

function ManageLinks({ help }: { help: Help }) {
  // web: iki mağazanın abonelikler sayfası da (telefonda hangisi açılırsa)
  return (
    <div className="flex flex-wrap gap-2">
      {(["apple", "google"] as const).map((k) => (
        <a key={k} className="btn-ghost text-xs" href={help.open[k].url} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={14} /> {STORE_NAMES[k]} aboneliklerim
        </a>
      ))}
    </div>
  );
}

function Detail({ s, help, onClose }: { s: AppSub; help: Help; onClose: () => void }) {
  const ui = useUI();
  const [picking, setPicking] = useState(false);
  const [editing, setEditing] = useState(false);
  const act = useSave((b: Record<string, unknown>) => call<{ item: AppSub | null }>("PUT", `/api/subscriptions/apps/${s.id}`, b),
    (_r, b) => { ui.toast(MSG[String(b.action)] ?? "Kaydedildi"); if (b.action === "degil" || b.action === "sifirla") onClose(); });
  const delSrc = useSave(() => call("DELETE", `/api/subscriptions/apps/sources/${s.source_id}`), () => { ui.toast("Abonelik silindi"); onClose(); });
  const run = (b: Record<string, unknown>) => act.mutate(b, { onError: (e) => ui.error(e) });
  const busy = act.isPending || delSrc.isPending;
  const detailCap = useCap("subscription_detail"); // tüm tahsilatlar, toplam, ay ay; yoksa son 12 tahsilat (eski sunucu)
  return (
    <>
      <Modal title={s.title} onClose={onClose} hidden={picking || editing}>
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-3">
            <SubLogoBox logo={s.logo} size={44} />
            <div className="min-w-0">
              <div className="font-semibold">{s.title}{s.plan ? <span className="font-normal text-stone-500"> · {s.plan}</span> : null}</div>
              <div className="text-stone-500">{s.store_label} · {amountLine(s)}</div>
            </div>
          </div>
          <Badges s={s} />
          <ul className="space-y-1 text-stone-600 dark:text-stone-300">
            {s.next_text && <li>{s.next_text}</li>}
            <li>{s.first_text}</li>
            {s.stale_text && <li>{s.stale_text}</li>}
            {s.cycle && s.yearly != null && <li>Yılda {tl(s.yearly)}{s.cycle === "yillik" && s.monthly != null ? ` · ayda ≈ ${tl(s.monthly)}` : ""}</li>}
            {s.price_up && <li className="flex items-center gap-1 text-red-600"><TrendingUp size={14} /> Zam: {tl(s.price_up.from)} → {tl(s.price_up.to)} ({dateTR(s.price_up.date)})</li>}
            {s.charged_after_cancel > 0 && s.cancelled_at && (
              <li className="font-medium text-red-600">{dateTR(s.cancelled_at)} iptalinden sonra {tl(s.charged_after_cancel)} çekildi. Mağazanın abonelikler sayfasından kontrol et.</li>
            )}
          </ul>

          {!s.name && s.guesses.length > 0 && (
            <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
              <div className="mb-1.5 text-xs text-stone-500">Bu tutar şunlardan biri olabilir (kesin değil):</div>
              <div className="flex flex-wrap gap-1.5">
                {s.guesses.map((g) => (
                  <button key={g.id!} type="button" disabled={busy} onClick={() => run({ action: "adlandir", catalog_id: g.id })}
                    className="flex min-h-9 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs dark:border-stone-700 dark:bg-stone-900">
                    <SubLogoBox logo={g} size={18} /> {g.plan}
                  </button>
                ))}
                <button type="button" disabled={busy} onClick={() => run({ action: "bilmiyorum" })} className="min-h-9 rounded-full px-2.5 py-1 text-xs text-stone-500 underline">Bilmiyorum</button>
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost text-xs" disabled={busy} onClick={() => setPicking(true)}>{s.name ? "Adını değiştir" : "Adını seç"}</button>
            {s.status === "aday" && <>
              <button className="btn-ghost text-xs" disabled={busy} onClick={() => run({ action: "abonelik", cycle: "aylik" })}>Her ay yenileniyor</button>
              <button className="btn-ghost text-xs" disabled={busy} onClick={() => run({ action: "abonelik", cycle: "yillik" })}>Her yıl yenileniyor</button>
            </>}
            {(s.status === "abonelik" || s.status === "aday") && (
              <button className="btn-danger text-xs" disabled={busy} onClick={() => run({ action: "iptal" })}>İptal ettim</button>
            )}
            {(s.status === "abonelik" || s.status === "aday") && !s.pending && (
              <button className="btn-ghost text-xs" disabled={busy}
                onClick={() => void ask("Tek seferlik uygulama satın alması mı? Abonelik toplamına ve hatırlatmalara girmez.").then((ok) => ok && run({ action: "degil" }))}>
                Abonelik değil (tek seferlik)
              </button>
            )}
            {(s.status === "iptal" || s.status === "degil") && (
              <button className="btn-ghost text-xs" disabled={busy} onClick={() => run({ action: s.status === "iptal" ? "aktif" : "sifirla" })}>Geri al</button>
            )}
            {s.pending && s.source_id != null && <>
              <button className="btn-ghost text-xs" disabled={busy} onClick={() => setEditing(true)}>Düzenle</button>
              <button className="btn-danger text-xs" disabled={busy}
                onClick={() => void ask(`${s.title} listeden silinsin mi?`).then((ok) => ok && delSrc.mutate(undefined, { onError: (e) => ui.error(e) }))}>Sil</button>
            </>}
          </div>

          <div className="border-t border-stone-100 pt-3 dark:border-stone-800">
            <div className="mb-1.5 text-xs text-stone-500">İptal ya da paket değişikliği mağazadan yapılır:</div>
            <ManageLinks help={help} />
          </div>

          {detailCap && !s.pending && (
            <div className="border-t border-stone-100 pt-3 dark:border-stone-800"><SubPaymentsFor r={{ app: s.id }}
              onCancel={() => void ask(`${s.title} "İptal ettim" olarak işaretlensin mi?\n\nSonra yine çekilirse seni uyarırız.`, { confirm: "İptal ettim", danger: false })
                .then((ok) => { if (ok) run({ action: "iptal" }); })} /></div>
          )}
          {!detailCap && s.charges.length > 0 && (
            <div className="border-t border-stone-100 pt-3 dark:border-stone-800">
              <div className="mb-1 text-xs font-semibold text-stone-500">Tahsilatlar</div>
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                {s.charges.map((c) => (
                  <li key={c.uid} className="flex justify-between py-1 text-xs"><span>{dateTR(c.date)} · {c.via === "kart" ? "kart ekstresi" : "hesap dökümü"}</span><span className="tabular-nums">{tl(c.amount)}</span></li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Modal>
      {picking && <CatalogPicker title={`${s.title} · adını seç`} onClose={() => setPicking(false)}
        onPick={(v) => { setPicking(false); run({ action: "adlandir", ...v }); }} />}
      {editing && s.source_id != null && <SubForm edit={s} onClose={() => setEditing(false)} />}
    </>
  );
}

const MSG: Record<string, string> = {
  adlandir: "Abonelik adlandırıldı", abonelik: "Abonelik olarak takip ediliyor", degil: "Abonelik değil olarak işaretlendi",
  iptal: "İptal ettim olarak işaretlendi; yine çekilirse uyarırız", aktif: "Abonelik yeniden aktif", bilmiyorum: "Adsız takip ediliyor",
  sifirla: "Geri alındı",
};

/** Elle ekle (katalogdan) ya da ekstreye düşmemiş aboneliği düzelt. */
function SubForm({ edit, onClose }: { edit?: AppSub; onClose: () => void }) {
  const ui = useUI();
  const cat = useCatalog();
  const [pick, setPick] = useState<{ catalog_id?: string; name?: string } | null>(edit ? (edit.catalog_id ? { catalog_id: edit.catalog_id } : { name: edit.title }) : null);
  const [picking, setPicking] = useState(!edit);
  const [amount, setAmount] = useState(edit?.amount != null ? String(edit.amount).replace(".", ",") : "");
  const [cycle, setCycle] = useState<"aylik" | "yillik">(edit?.cycle === "yillik" ? "yillik" : "aylik");
  const [next, setNext] = useState(edit?.next_date ?? "");
  const [store, setStore] = useState<"apple" | "google" | "diger">((edit?.store as "apple" | "google" | "diger") ?? "apple");
  const picked = pick?.catalog_id ? cat.data?.items.find((x) => x.id === pick.catalog_id) : null;
  const n = parseAmount(amount);
  const body = { ...(pick?.catalog_id ? { catalog_id: pick.catalog_id } : { name: pick?.name }), amount: n, cycle, next_date: next, store };
  const save = useSave(() => (edit ? call("PATCH", `/api/subscriptions/apps/sources/${edit.source_id}`, body) : call("POST", "/api/subscriptions/apps/manual", body)),
    () => { ui.toast(edit ? "Abonelik güncellendi" : "Abonelik eklendi"); onClose(); });
  return (
    <>
      <Modal title={edit ? "Aboneliği düzenle" : "Abonelik ekle"} onClose={onClose} hidden={picking} footer={<>
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={!pick || !(n > 0) || !next || save.isPending}
          onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e) })}>Kaydet</button>
      </>}>
        <div className="space-y-3">
          <button type="button" className="flex w-full items-center gap-2 rounded-lg border border-stone-200 p-2 text-left dark:border-stone-700" onClick={() => setPicking(true)}>
            {picked ? <SubLogoBox logo={picked} size={28} /> : null}
            <span className="flex-1">{picked?.name ?? pick?.name ?? "Katalogdan seç"}</span>
            <span className="text-xs text-stone-500">Değiştir</span>
          </button>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="as-amount">Tutar (₺)</label>
              <input id="as-amount" className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="ör. 79,99" />
            </div>
            <div>
              <label className="label" htmlFor="as-next">Sonraki yenileme</label>
              <input id="as-next" type="date" className="input" value={next} onChange={(e) => setNext(e.target.value)} />
            </div>
          </div>
          <Segmented label="Dönem" value={cycle} onChange={setCycle} options={[{ value: "aylik", label: "Her ay" }, { value: "yillik", label: "Her yıl" }]} />
          <div>
            <label className="label" htmlFor="as-store">Nereden ödeniyor</label>
            <select id="as-store" className="input" value={store} onChange={(e) => setStore(e.target.value as typeof store)}>
              <option value="apple">App Store</option><option value="google">Google Play</option><option value="diger">Kart ya da banka (mağaza dışı)</option>
            </select>
          </div>
          <p className="text-xs text-stone-500">Ekstreye düşünce aynı tutar ve günle eşleşir, tek satır olur.</p>
        </div>
      </Modal>
      {picking && <CatalogPicker title="Abonelik seç" onClose={() => { setPicking(false); if (!pick) onClose(); }} onPick={(v) => { setPick(v); setPicking(false); }} />}
    </>
  );
}

interface ShotResult { rows: { name: string; amount: number | null; active: boolean; match: { id: string; pending: boolean; title: string } | null }[]; message: string }

function Screenshot({ help, onClose }: { help: Help; onClose: () => void }) {
  const ui = useUI();
  const [tab, setTab] = useState<"apple" | "google">("apple");
  const [files, setFiles] = useState<File[]>([]);
  const [res, setRes] = useState<ShotResult | null>(null);
  const up = useSave(async () => {
    const fd = new FormData();
    for (const f of files) fd.append("files", await shrink(f, 2000), f.name.replace(/\.\w+$/, "") + ".jpg");
    fd.append("store", tab);
    return send<ShotResult>("/api/subscriptions/apps/screenshot", { method: "POST", body: fd });
  }, (r) => { setRes(r); ui.toast(r.message); });
  return (
    <Modal title="Ekran görüntüsünden tanıt" onClose={onClose}>
      <div className="space-y-3 text-sm">
        <p className="text-stone-600 dark:text-stone-300">{help.text}</p>
        <Segmented label="Telefon" value={tab} onChange={setTab} options={[{ value: "apple", label: "iPhone" }, { value: "google", label: "Android" }]} />
        <div>
          <div className="mb-1 font-medium">Bu ekranı nasıl bulurum?</div>
          <ol className="list-decimal space-y-0.5 pl-5 text-stone-600 dark:text-stone-300">{help.steps[tab].map((x) => <li key={x}>{x}</li>)}</ol>
          <a className="btn-ghost mt-2 text-xs" href={help.open[tab].url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> {help.open[tab].label}</a>
        </div>
        <label className="block">
          <span className="label">Ekran görüntüleri (en çok 5)</span>
          <input type="file" accept="image/*" multiple className="input" onChange={(e) => setFiles([...(e.target.files ?? [])].slice(0, 5))} />
        </label>
        <button className="btn-primary w-full" disabled={!files.length || up.isPending} onClick={() => up.mutate(undefined, { onError: (e) => ui.error(e) })}>
          <Camera size={16} /> {up.isPending ? "Okunuyor…" : `${files.length || ""} görüntüyü oku`.trim()}
        </button>
        {res && (
          <div className="rounded-lg bg-stone-50 p-2.5 dark:bg-stone-800/60">
            <p className="mb-1 font-medium">{res.message}</p>
            <ul className="space-y-0.5 text-xs">
              {res.rows.map((r, i) => (
                <li key={i} className="flex justify-between gap-2"><span>{r.name}{r.amount != null ? ` · ${tl(r.amount)}` : ""}</span>
                  <span className="shrink-0 text-stone-500">{!r.active ? "etkin değil" : !r.match ? "—" : r.match.pending ? "ekstrede henüz yok" : "eşleşti"}</span></li>
              ))}
            </ul>
          </div>
        )}
        {help.mail.available && (
          <details className="rounded-lg border border-stone-200 p-2.5 text-xs dark:border-stone-700">
            <summary className="cursor-pointer font-medium">{help.mail.title}</summary>
            <p className="mt-1 text-stone-600 dark:text-stone-300">{help.mail.text}</p>
            <code className="mt-1 block break-all rounded bg-stone-100 p-1.5 dark:bg-stone-800">{help.mail.filter}</code>
          </details>
        )}
      </div>
    </Modal>
  );
}

/** Tasarruf → "Uygulama mağazası" bölümü (+ elle eklenen mağaza dışı abonelikler). */
export default function AppSubs({ q }: { q: { data?: AppsData; isLoading: boolean } }) {
  const [open, setOpen] = useState<string | null>(null);
  const [shot, setShot] = useState(false);
  const [adding, setAdding] = useState(false);
  const d = q.data;
  if (!d) return null;
  const all = [...d.items, ...d.other];
  const cur = all.find((x) => x.id === open) ?? null;
  return (
    <section className="card overflow-hidden" aria-labelledby="as-h">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-stone-100 px-4 py-2.5 dark:border-stone-800">
        <div className="min-w-0">
          <h2 id="as-h" className="font-semibold">Uygulama mağazası</h2>
          <p className="text-xs text-stone-500">App Store ve Google Play tahsilatları tek tek; adı bilinmese de takip edilir.</p>
          {d.totals.count > 0 && (
            <p className="mt-1 text-sm font-medium tabular-nums">Uygulama abonelikleri: ayda {tl(d.totals.monthly)} · yılda {tl(d.totals.yearly)}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-1.5">
          <button className="btn-ghost text-xs" onClick={() => setShot(true)}><Camera size={14} /> Ekran görüntüsü</button>
          <button className="btn-ghost text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Ekle</button>
        </div>
      </div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {all.map((s) => <Row key={s.id} s={s} onOpen={() => setOpen(s.id)} />)}
        {!all.length && (
          <li className="px-4 py-4 text-sm text-stone-500">
            Ekstrede App Store / Google Play tahsilatı yok. Abonelik listenin ekran görüntüsünü yükleyerek ya da elle ekleyebilirsin.
          </li>
        )}
      </ul>
      {cur && <Detail s={cur} help={d.help} onClose={() => setOpen(null)} />}
      {shot && <Screenshot help={d.help} onClose={() => setShot(false)} />}
      {adding && <SubForm onClose={() => setAdding(false)} />}
    </section>
  );
}
