import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronRight, Coins, FlaskConical, LayoutDashboard, Megaphone, MessageSquareWarning, Receipt, ShieldCheck, Smartphone, TableProperties, Users as UsersIcon, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { AiCosts as Costs } from "../api";
import { useFeedbackCounts } from "../components/AdminFeedback";
import AdminSupport, { useSupportCounts } from "../components/AdminSupport";
import AiCosts from "../components/AiCosts";
import Announce from "../components/Announce";
import AppUpdate from "../components/AppUpdate";
import CardTemplates from "../components/CardTemplates";
import DemoStats from "../components/DemoStats";
import Olcum from "../components/Olcum";
import PlanTable from "../components/PlanTable";
import ReceiptTemplates from "../components/ReceiptTemplates";
import UpdateNotice from "../components/UpdateNotice";
import Users from "../components/Users";
import { useIsAdmin } from "../components/hooks";
import { call, send } from "../lib/apiError";
import { cx } from "../format";

// Yönetici paneli (#/yonetici/<alt sayfa>): eskiden Ayarlar'ın altındaki yönetici bölümleri burada, her biri kendi
// sayfasında. Adres alt sayfayı tutar: yenileyince aynı sayfa açılır. Bölümlerin kendisi (bileşenler) değişmedi.

type Sub = "genel" | "olcum" | "kullanicilar" | "geri-bildirim" | "duyuru" | "yapay-zeka" | "paket-tablosu" | "sablonlar" | "mobil" | "demo";

const SUBS: { key: Sub; label: string; icon: LucideIcon; hint: string }[] = [
  { key: "genel", label: "Genel", icon: LayoutDashboard, hint: "Özet ve sürüm" },
  { key: "olcum", label: "Ölçüm", icon: Activity, hint: "Demo → hesap → aktivasyon → tutunma → abonelik" },
  { key: "kullanicilar", label: "Kullanıcılar", icon: UsersIcon, hint: "Hesaplar, davetler, paketler, silinenler" },
  { key: "geri-bildirim", label: "Destek", icon: MessageSquareWarning, hint: "Kullanıcı yazışmaları, ekstre bildirimleri" },
  { key: "duyuru", label: "Duyuru gönder", icon: Megaphone, hint: "Telefonlara bildirim: yeni sürüm, duyuru" },
  { key: "yapay-zeka", label: "Yapay zekâ maliyeti", icon: Coins, hint: "Aylık maliyet, kullanıcı başı, sınırla" },
  { key: "paket-tablosu", label: "Paket tablosu", icon: TableProperties, hint: "Hangi sayfa hangi pakette" },
  { key: "sablonlar", label: "Fiş şablonları", icon: Receipt, hint: "Yapay zekâsız okunan zincirler" },
  { key: "mobil", label: "Mobil sürüm", icon: Smartphone, hint: "Son / en düşük sürüm, mağaza" },
  { key: "demo", label: "Canlı demo", icon: FlaskConical, hint: "Demo kullanımı" },
];

const readSub = (): Sub => {
  const s = window.location.hash.replace(/^#\/?/, "").split("?")[0].split("/")[1] as Sub;
  return SUBS.some((x) => x.key === s) ? s : "genel";
};
const goSub = (s: Sub) => { window.location.hash = `/yonetici/${s}`; window.scrollTo(0, 0); };

export default function Admin() {
  const admin = useIsAdmin();
  const auth = useQuery({ queryKey: ["auth"], queryFn: () => send<unknown>("/api/auth/status") });
  const [sub, setSub] = useState<Sub>(readSub);
  useEffect(() => {
    const on = () => setSub(readSub());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const fb = useFeedbackCounts();
  const sup = useSupportCounts(); // destek yazışması varsa rozet okunmamış yazışma sayısı
  if (auth.isLoading) return <></>;
  if (!admin) {
    return (
      <div className="card mx-auto mt-6 max-w-md space-y-2 p-6 text-center">
        <h1 className="text-lg font-semibold">Yönetici paneli</h1>
        <p className="text-sm text-stone-500">Bu sayfa yalnız sunucu yöneticisine açık.</p>
      </div>
    );
  }
  const cur = SUBS.find((x) => x.key === sub)!;
  const badge = (k: Sub) => (k !== "geri-bildirim" ? null : (sup ? sup.okunmamis : fb.data?.yeni) || null);
  return (
    <div className="space-y-4">
      <h1 className="flex items-center gap-2 text-xl font-semibold"><ShieldCheck size={22} aria-hidden /> Yönetici paneli</h1>
      {/* dar ekran: açılır seçici (yatay taşma yok); geniş ekran: solda sekmeler */}
      <div className="md:hidden">
        <label className="label" htmlFor="admin-sub">Bölüm</label>
        <select id="admin-sub" className="input" value={sub} onChange={(e) => goSub(e.target.value as Sub)}>
          {SUBS.map((x) => <option key={x.key} value={x.key}>{x.label}{badge(x.key) ? ` (${badge(x.key)} yeni)` : ""}</option>)}
        </select>
      </div>
      <div className="md:flex md:items-start md:gap-4">
        <nav aria-label="Yönetici bölümleri" className="sticky top-4 hidden w-48 shrink-0 space-y-0.5 md:block">
          {SUBS.map((x) => (
            <a key={x.key} href={`#/yonetici/${x.key}`} aria-current={sub === x.key ? "page" : undefined}
              className={cx("flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm",
                sub === x.key ? "bg-white font-medium shadow-sm dark:bg-stone-800" : "text-stone-600 hover:bg-white/60 dark:text-stone-400 dark:hover:bg-stone-800/50")}>
              <x.icon size={16} aria-hidden /> <span className="min-w-0 flex-1">{x.label}</span>
              {badge(x.key) && <span className="rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold text-white tabular-nums">{badge(x.key)}</span>}
            </a>
          ))}
        </nav>
        <div className="min-w-0 flex-1 space-y-4">
          <h2 className="sr-only">{cur.label}</h2>
          {sub === "genel" && <Overview />}
          {sub === "olcum" && <Olcum />}
          {sub === "kullanicilar" && <Users />}
          {sub === "geri-bildirim" && <AdminSupport />}
          {sub === "duyuru" && <Announce />}
          {sub === "yapay-zeka" && <AiCosts />}
          {sub === "paket-tablosu" && <PlanTable />}
          {sub === "sablonlar" && <><ReceiptTemplates /><CardTemplates /></>}
          {sub === "mobil" && <AppUpdate />}
          {sub === "demo" && <Demo />}
        </div>
      </div>
    </div>
  );
}

const tl = (x: number) => `₺${x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

interface AdminUsers { users: { deleted_at?: string | null; disabled: boolean; plan?: string }[]; invites: { used_at: string | null }[]; hosted?: boolean }

/** Genel: sayılarla özet, her kutu kendi sayfasına götürür; sürüm uyarısı. */
function Overview() {
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => call<AdminUsers>("GET", "/api/admin/users") });
  const costs = useQuery({ queryKey: ["admin-ai-costs", null], queryFn: () => call<Costs>("GET", "/api/admin/ai-costs") });
  const fb = useFeedbackCounts();
  const u = users.data;
  const active = u?.users.filter((x) => !x.deleted_at && !x.disabled) ?? [];
  const paid = active.filter((x) => x.plan && x.plan !== "free").length;
  const tiles: { label: string; value: string; note?: string; to: Sub; alert?: boolean }[] = [
    { label: "Kullanıcı", value: u ? String(active.length) : "…", to: "kullanicilar",
      note: u ? `${u.invites.filter((i) => !i.used_at).length} bekleyen davet${u.hosted ? ` · ${paid} ücretli paket` : ""}` : undefined },
    { label: "Yeni geri bildirim", value: fb.data ? String(fb.data.yeni) : "…", to: "geri-bildirim", alert: !!fb.data?.yeni,
      note: fb.data ? `${fb.data.okundu} okundu · ${fb.data.cozuldu} çözüldü` : undefined },
    { label: "Bu ay yapay zekâ", value: costs.data ? tl(costs.data.cost_try) : "…", to: "yapay-zeka", alert: !!costs.data?.over_alert,
      note: costs.data ? `${costs.data.calls.toLocaleString("tr-TR")} çağrı${costs.data.over_alert ? ` · ${costs.data.over_alert} kişi eşiği aştı` : ""}` : undefined },
  ];
  return (
    <>
      <UpdateNotice />
      <section className="grid gap-3 sm:grid-cols-3">
        {tiles.map((t) => (
          <button key={t.label} onClick={() => goSub(t.to)}
            className={cx("card p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50", t.alert && "border-amber-300 dark:border-amber-800")}>
            <span className="block text-xs text-stone-500">{t.label}</span>
            <span className="block text-2xl font-semibold tabular-nums">{t.value}</span>
            {t.note && <span className="block text-xs text-stone-500">{t.note}</span>}
          </button>
        ))}
      </section>
      <OfficialPrices />
      <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
        {SUBS.filter((x) => x.key !== "genel").map((x) => (
          <li key={x.key}>
            <a href={`#/yonetici/${x.key}`} className="flex items-center gap-3 px-4 py-3">
              <x.icon size={18} aria-hidden />
              <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{x.label}</span>
                <span className="block text-xs text-stone-500">{x.hint}</span></span>
              <ChevronRight size={16} className="text-stone-400" aria-hidden />
            </a>
          </li>
        ))}
      </ul>
      <p className="text-xs text-stone-500">Yönetici yalnız hesapları ve sunucu ayarlarını görür; kullanıcıların harcamaları burada görünmez.</p>
    </>
  );
}

interface OfficialStatus { configured: boolean; last_day: string | null; regions: string[]; rows: number; imported_at: string | null;
  last_run_at: string | null; last_error: string | null; last_error_at: string | null;
  /** veri kalite denetimi (yeni sunucu): atılan aykırı teklif, alınmayan günler, tutarsız gruplar */
  outliers?: number; anomalies?: { day: string; region: string; note: string | null; at: string }[];
  inconsistent_groups?: { day: string | null; groups: { group_key: string; median: number; dropped: number }[] };
  /** topluluk fiyatları → BigQuery (yeni sunucu) */
  community_export?: { configured: boolean; last_day: string | null; rows: number; exported_at: string | null;
    last_run_at: string | null; last_error: string | null; last_error_at: string | null };
  /** ürün gruplama kalitesi ve topluluk ↔ Market Fiyatı uyuşmazlığı (yeni sunucu) */
  grouping_quality?: GroupingQuality; disagreements?: Disagreement[] }
export interface GroupingQuality {
  names: number; grouped: number; grouped_pct: number; pending: number; by_source?: Record<string, number>;
  conflicts?: { count: number; top: { form: string; groups: { key: string; label: string; n: number }[]; resolved_to: string | null }[] };
  wide_spread?: { count: number; top: { group_key: string; label: string; min: number; max: number; ratio: number }[] };
  catalog?: { products: number; brands: number; updated_at: string | null } | null;
}
export interface Disagreement { chain: string; group_key: string; label: string; days: number; community: number; official: number; pct: number }
const SOURCE_TR: Record<string, string> = { rule: "kural", ai: "yapay zekâ", catalog: "katalog", override: "elle eşleme" };

/** Gruplama kalitesi: gruplanan oran, kaynaklar, çelişen gruplar, fiyat aralığı geniş gruplar, tutarsız ürünler. */
export function GroupingQualityPanel({ g, dis }: { g?: GroupingQuality; dis?: Disagreement[] }) {
  if (!g && !dis?.length) return null;
  const pctf = (n: number) => `%${n.toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;
  return (
    <div className="space-y-1.5 border-t border-stone-100 pt-1.5 dark:border-stone-800" data-testid="gruplama-kalitesi">
      {g && <>
        <h3 className="text-xs font-semibold text-stone-600 dark:text-stone-300">Ürün gruplama</h3>
        <p className="text-xs text-stone-500 tabular-nums">{g.grouped.toLocaleString("tr-TR")} / {g.names.toLocaleString("tr-TR")} ad gruplandı ({pctf(g.grouped_pct)}) · {g.pending.toLocaleString("tr-TR")} bekliyor
          {g.by_source && ` · ${Object.entries(g.by_source).filter(([, n]) => n).map(([k, n]) => `${SOURCE_TR[k] ?? k} ${n.toLocaleString("tr-TR")}`).join(", ")}`}</p>
        {g.catalog && <p className="text-xs text-stone-500 tabular-nums">Katalog: {g.catalog.products.toLocaleString("tr-TR")} ürün · {g.catalog.brands.toLocaleString("tr-TR")} marka{g.catalog.updated_at ? ` · ${new Date(g.catalog.updated_at).toLocaleDateString("tr-TR")}` : ""}</p>}
        {!!g.conflicts?.count && (
          <details className="text-xs" data-testid="celisen-gruplar">
            <summary className="cursor-pointer text-amber-800 dark:text-amber-300">Çelişen gruplar · {g.conflicts.count}</summary>
            <ul className="mt-1 space-y-1">
              {g.conflicts.top.map((c) => (
                <li key={c.form} className="break-words"><b className="font-medium">{c.form}</b>: {c.groups.map((x) => `${x.label} (${x.n})`).join(" · ")}{c.resolved_to ? ` → ${c.resolved_to}` : ""}</li>
              ))}
            </ul>
          </details>
        )}
        {!!g.wide_spread?.count && (
          <details className="text-xs" data-testid="genis-aralik">
            <summary className="cursor-pointer text-stone-600 dark:text-stone-300">Fiyat aralığı 3 katı aşan gruplar · {g.wide_spread.count}</summary>
            <table className="mt-1 w-full tabular-nums">
              <thead><tr className="text-left text-stone-500"><th className="font-normal">Grup</th><th className="text-right font-normal">En düşük</th><th className="text-right font-normal">En yüksek</th><th className="text-right font-normal">Kat</th></tr></thead>
              <tbody>{g.wide_spread.top.map((w) => (
                <tr key={w.group_key}><td className="pr-2 break-words">{w.label}</td><td className="text-right">{tl(w.min)}</td><td className="text-right">{tl(w.max)}</td><td className="text-right">{w.ratio.toLocaleString("tr-TR", { maximumFractionDigits: 1 })}×</td></tr>
              ))}</tbody>
            </table>
          </details>
        )}
      </>}
      {!!dis?.length && (
        <details className="text-xs" data-testid="tutarsiz-urunler">
          <summary className="cursor-pointer text-amber-800 dark:text-amber-300">Tutarsız ürünler (kullanıcılar ↔ Market Fiyatı) · {dis.length}</summary>
          <table className="mt-1 w-full tabular-nums">
            <thead><tr className="text-left text-stone-500"><th className="font-normal">Ürün</th><th className="text-right font-normal">Kullanıcılar</th><th className="text-right font-normal">Market F.</th><th className="text-right font-normal">Fark</th></tr></thead>
            <tbody>{dis.map((d) => (
              <tr key={`${d.chain}-${d.group_key}`}><td className="pr-2 break-words">{d.chain} · {d.label} <span className="text-stone-500">({d.days} gün)</span></td>
                <td className="text-right">{tl(d.community)}</td><td className="text-right">{tl(d.official)}</td><td className="text-right">{d.pct > 0 ? "+" : ""}%{Math.round(d.pct)}</td></tr>
            ))}</tbody>
          </table>
        </details>
      )}
    </div>
  );
}

/** Resmî fiyatlar (BigQuery → sunucu, gecelik): son içe aktarılan gün, iller, satır, son hata. Eski sunucuda (404) yok. */
function OfficialPrices() {
  const q = useQuery({ queryKey: ["admin-official-prices"], retry: false,
    queryFn: () => call<OfficialStatus>("GET", "/api/admin/official-prices") });
  const s = q.data;
  if (!s || (!s.configured && !s.last_day)) return null;
  const day = (iso: string | null) => (iso ? new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleString("tr-TR", iso.length === 10 ? { day: "numeric", month: "long" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
  return (
    <section className={cx("card space-y-1.5 p-4 text-sm", s.last_error && "border-amber-300 dark:border-amber-800")} data-testid="resmi-fiyatlar">
      <h2 className="font-semibold">Resmî fiyatlar</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-stone-500">Son gün</dt><dd className="tabular-nums">{day(s.last_day)}</dd>
        <dt className="text-stone-500">İller</dt><dd className="break-words">{s.regions.length ? `${s.regions.length} · ${s.regions.join(", ")}` : "—"}</dd>
        <dt className="text-stone-500">Satır</dt><dd className="tabular-nums">{s.rows.toLocaleString("tr-TR")}</dd>
        <dt className="text-stone-500">Son deneme</dt><dd className="tabular-nums">{day(s.last_run_at)}</dd>
      </dl>
      {s.last_error && <p className="rounded bg-amber-50 px-2 py-1 text-xs break-words text-amber-900 dark:bg-amber-950 dark:text-amber-200">Son hata ({day(s.last_error_at)}): {s.last_error}</p>}
      {!s.configured && <p className="text-xs text-stone-500">BigQuery kimliği ayarlı değil; eski veriler gösteriliyor.</p>}
      {!!s.anomalies?.length && (
        <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200" data-testid="resmi-anomali">
          <p className="font-medium">Alınmayan günler (önceki veri korunuyor)</p>
          <ul className="mt-0.5 space-y-0.5">
            {s.anomalies.map((a) => <li key={`${a.day}-${a.region}`} className="break-words">{day(a.day)} · {a.region}: {a.note}</li>)}
          </ul>
        </div>
      )}
      {!!s.inconsistent_groups?.groups.length && (
        <details className="text-xs" data-testid="tutarsiz-gruplar">
          <summary className="cursor-pointer text-stone-600 dark:text-stone-300">
            Tutarsız gruplar · {day(s.inconsistent_groups.day)} · {(s.outliers ?? 0).toLocaleString("tr-TR")} teklif atıldı</summary>
          <table className="mt-1 w-full tabular-nums">
            <thead><tr className="text-left text-stone-500"><th className="font-normal">Grup</th><th className="text-right font-normal">Ortanca</th><th className="text-right font-normal">Atılan</th></tr></thead>
            <tbody>
              {s.inconsistent_groups.groups.map((g) => (
                <tr key={g.group_key}><td className="pr-2 break-all">{g.group_key}</td>
                  <td className="text-right">{tl(g.median)}</td><td className="text-right">{g.dropped}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <GroupingQualityPanel g={s.grouping_quality} dis={s.disagreements} />
      {s.community_export && (s.community_export.configured || s.community_export.last_day) && (
        <div className="border-t border-stone-100 pt-1.5 dark:border-stone-800" data-testid="topluluk-aktarim">
          <h3 className="text-xs font-semibold text-stone-600 dark:text-stone-300">Topluluk fiyatları → BigQuery</h3>
          <p className="text-xs text-stone-500 tabular-nums">Son gün {day(s.community_export.last_day)} · {s.community_export.rows.toLocaleString("tr-TR")} satır · son deneme {day(s.community_export.last_run_at)}</p>
          {s.community_export.last_error && <p className="mt-0.5 rounded bg-amber-50 px-2 py-1 text-xs break-words text-amber-900 dark:bg-amber-950 dark:text-amber-200">Son hata ({day(s.community_export.last_error_at)}): {s.community_export.last_error}</p>}
        </div>
      )}
    </section>
  );
}

/** Canlı demo: yapılandırılmamışsa bölüm boş kalmasın. */
function Demo() {
  const q = useQuery({ queryKey: ["demo-stats", "7"], retry: false,
    queryFn: () => send<{ configured?: boolean }>("/api/admin/demo-stats?days=7") });
  return (
    <>
      <DemoStats />
      {q.data && !q.data.configured && (
        <section className="card p-4 text-sm text-stone-500">Bu sunucuya bağlı canlı demo yok; demo istatistikleri demo sunucusu ayarlanınca burada görünür.</section>
      )}
    </>
  );
}
