import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Coins, FlaskConical, LayoutDashboard, MessageSquareWarning, Receipt, ShieldCheck, Smartphone, TableProperties, Users as UsersIcon, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { AiCosts as Costs } from "../api";
import AdminFeedback, { useFeedbackCounts } from "../components/AdminFeedback";
import AiCosts from "../components/AiCosts";
import AppUpdate from "../components/AppUpdate";
import DemoStats from "../components/DemoStats";
import PlanTable from "../components/PlanTable";
import ReceiptTemplates from "../components/ReceiptTemplates";
import UpdateNotice from "../components/UpdateNotice";
import Users from "../components/Users";
import { useIsAdmin } from "../components/hooks";
import { call, send } from "../lib/apiError";
import { cx } from "../format";

// Yönetici paneli (#/yonetici/<alt sayfa>): eskiden Ayarlar'ın altındaki yönetici bölümleri burada, her biri kendi
// sayfasında. Adres alt sayfayı tutar: yenileyince aynı sayfa açılır. Bölümlerin kendisi (bileşenler) değişmedi.

type Sub = "genel" | "kullanicilar" | "geri-bildirim" | "yapay-zeka" | "paket-tablosu" | "sablonlar" | "mobil" | "demo";

const SUBS: { key: Sub; label: string; icon: LucideIcon; hint: string }[] = [
  { key: "genel", label: "Genel", icon: LayoutDashboard, hint: "Özet ve sürüm" },
  { key: "kullanicilar", label: "Kullanıcılar", icon: UsersIcon, hint: "Hesaplar, davetler, paketler, silinenler" },
  { key: "geri-bildirim", label: "Geri bildirim", icon: MessageSquareWarning, hint: "Sorun bildir mesajları" },
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
  const badge = (k: Sub) => (k === "geri-bildirim" && fb.data?.yeni ? fb.data.yeni : null);
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
          {sub === "kullanicilar" && <Users />}
          {sub === "geri-bildirim" && <AdminFeedback />}
          {sub === "yapay-zeka" && <AiCosts />}
          {sub === "paket-tablosu" && <PlanTable />}
          {sub === "sablonlar" && <ReceiptTemplates />}
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
