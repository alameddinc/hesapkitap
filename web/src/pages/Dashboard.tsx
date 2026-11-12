import { useQuery } from "@tanstack/react-query";
import { rowTotals } from "../lib/counts";
import { TERIM, faizGeliriNotu } from "../lib/terimler";
import TerimBilgi from "../components/TerimBilgi";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Plus, Receipt, Settings2, Type, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type Insight, type Row, type VsPrev } from "../api";
import BarList from "../components/BarList";
import { BudgetForm, BudgetList } from "../components/Budgets";
import Insights, { HiddenInsights } from "../components/Insights";
import Kpi from "../components/Kpi";
import NetWorth, { ZakatDue } from "../components/NetWorth";
import Onboarding from "../components/Onboarding";
import Reminders from "../components/Reminders";
import SharedInbox from "../components/SharedInbox";
import MonthPicker from "../components/MonthPicker";
import MonthDigest from "../components/MonthDigest";
import Features from "../components/Features";
import SimpleDashboard from "../components/SimpleDashboard";
import Compare from "../components/Compare";
import YearSummary from "../components/YearSummary";
import YearShortcut from "../components/YearShortcut";
import Jars from "../components/Jars";
import Holdings from "../components/Holdings";
import { InterestHistory, TrendChart } from "./Stats";
import { EstimateNote, isEstimate } from "../components/Estimates";
import { PlanGate } from "../components/Plan";
import { inSharedBook, routeVisible, useBook, useDemo, useMeta, useUiPrefs } from "../components/hooks";
import Payables from "../components/Payables";
import TxnList from "../components/TxnList";
import TaksitSatiri from "../components/Taksitler";
import { DonemSonuKarti } from "../components/Beklenen";
import DebtPlanCard from "../components/DebtPlanCard";
import SadakaOzetKarti from "../components/SadakaOzet";
import FaizOzetKarti from "../components/FaizOzet";
import { gorunumDegistir, kayitDuzeni, ozetDuzeni, sadeMi, sakliDuzen, type Gorunum } from "../lib/ozetDuzen";
import { GORUNUM_ACIKLAMA, GORUNUM_ADI, gorunumAcildi, gorunumDegisti } from "../lib/gorunum";
import Segmented from "../components/Segmented";
import TekBakis, { AyrintiBaslik, ayrintiAcikMi, ayrintiKaydet } from "../components/TekBakis";
import { boundsOf, cx, signed, tl, tlCompact, periodLabel } from "../format";
import { useUI } from "../ui";
import SorguDurumu, { KartIskelet, SatirIskelet } from "../components/SorguDurumu";

/** Seçili işlemlerin toplamı: gider ve gelir sunucu bayrağıyla (gider satırları toplamı; yalnız gelirse gelir toplamı). */
const detailTotal = (rows: Row[]) => { const t = rowTotals(rows); return t.spend || t.income; };

/** Hazır sürüm (posta adresi açık): bu dönem son ödemeli faturaların toplamı; dokununca Ödemeler'e kayar. */
function BillsLine({ month }: { month: string }) {
  const ui = useUI();
  const meta = useMeta().data;
  const on = !!meta?.plan?.hosted && meta.plan.features.posta !== false;
  const q = useQuery({ queryKey: ["bills"], queryFn: api.bills, enabled: on });
  if (!on) return null;
  if (!q.data) return <SorguDurumu q={q} kompakt etiket="Faturalar yükleniyor" />;
  const [from, to] = boundsOf(month, meta);
  const items = q.data.filter((b) => b.due_date >= from && b.due_date <= to);
  if (!items.length) return null;
  const total = items.reduce((a, b) => a + b.amount, 0);
  const current = meta?.current_month === month;
  return (
    <button className="card flex w-full items-center gap-2 p-3 text-left text-sm active:bg-stone-50 dark:active:bg-stone-800"
      onClick={() => {
        const el = document.getElementById("w-odemeler");
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        else ui.toast("Ödemeler Özet'te kapalı; Düzenle'den ekleyebilirsin");
      }}>
      <Receipt size={16} className="shrink-0 text-stone-500" />
      <span className="min-w-0 flex-1">{current ? "Bu ay gelen faturalar" : `${periodLabel(month)} faturaları`}:{" "}
        <b className="tabular-nums">{tl(total)}</b> <span className="text-stone-500">({items.length})</span></span>
      <ChevronRight size={16} className="text-stone-400" />
    </button>
  );
}

/** "Geçen dönemin aynı gününe göre ₺812 az": sunucu vs_prev (başlıktaki giderle aynı sayım ve aynı dönem tanımı);
 *  biten dönemde "Ekim dönemine göre". Karşılaştırılacak harcama yoksa satır yok. */
export function kiyasMetni(v: VsPrev | null | undefined): { text: string; up: boolean } | null {
  if (!v || v.prev <= 0 || Math.abs(v.diff) < 1) return v && v.prev > 0 ? { text: v.basis === "same_day" ? "Geçen dönemin aynı günüyle aynı" : `${v.prev_name} dönemiyle aynı`, up: false } : null;
  const ne = `${tl(Math.abs(v.diff)).replace(/,\d\d(?=\D*$)/, "")} ${v.diff > 0 ? "fazla" : "az"}`;
  return { text: `${v.basis === "same_day" ? "Geçen dönemin aynı gününe" : `${v.prev_name} dönemine`} göre ${ne}${v.incomplete ? " · kart ekstresi gelince değişir" : ""}`, up: v.diff > 0 };
}

type Sel = { by: "category" | "merchant" | "card"; name: string } | null;

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="card p-3 sm:p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-semibold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

// Özet bileşenleri: sırası ve hangilerinin görüneceği kişiye özel (Düzenle). module: ait olduğu özellik/sayfa.
export const WIDGETS: Record<string, { title: string; module?: string; cap?: string }> = {
  // sade düzenin üstü (hep en üstte, kaldırılmaz): Harcanabilir + dönem sonu tahmini, uyarı şeridi, en önemli 3 satır
  sade: { title: "Tek bakış" },
  // ayraç (kaldırılmaz, taşınır): ardındakiler kapalı "Ayrıntılar" bölümünde
  ayrinti: { title: "Ayrıntılar" },
  kartlar: { title: "Gelir · gider · net · faiz" },
  // dokununca Faiz ve ücretler; sunucu interest_paid vermiyorsa (eski sunucu) listede yok
  "faiz-ucret": { title: "Son ekstrelerdeki faiz ve ücret", cap: "summary_interest_paid" },
  "donem-sonu": { title: "Dönem sonu", module: "plan" },
  gelenler: { title: "Telefondan gelenler" },
  baslarken: { title: "Başlarken" },
  uyarilar: { title: "Dikkat etmen gerekenler" },
  net: { title: "Net durum ve hesaplar", module: "hesaplar" },
  zekat: { title: "Zekât borcu", module: "zekat" },
  butceler: { title: "Bütçeler" },
  kategoriler: { title: "Kategoriye göre" },
  isyerleri: { title: "En çok harcanan yerler" },
  "istatistik-link": { title: "İstatistik bağlantısı", module: "istatistik" },
  odemeler: { title: "Ödemeler" },
  trend: { title: "Aylara göre gelir ve gider", module: "istatistik" },
  karsilastirma: { title: "Geçen dönemlere göre", module: "istatistik" },
  yillik: { title: "Yıllık özet", module: "istatistik" },
  faiz: { title: "Faiz alarmı geçmişi", module: "istatistik" },
  kumbaralar: { title: "Kumbaralar", module: "hesaplar" },
  altin: { title: "Altın ve döviz", module: "hesaplar" },
  "son-islemler": { title: "Son işlemler" },
  // varsayılan düzende yok: Borç planı ekranındaki "Özet'te göster" ya da Özeti düzenle ile eklenir; ≥2 açık borçta görünür
  "borc-plani": { title: "Borç planı", cap: "debt_plan" },
  // ritim (Her Cuma / Ayda bir) seçiliyse son sadakadan beri geçen süre ve süreklilik; seçim yoksa görünmez
  sadaka: { title: "Sadaka", module: "zekat", cap: "sadaka_reminder" },
};
// Varsayılan görünüm Minimal: üstte Tek bakış (ve kurulum / telefondan gelenler: yalnız iş varken görünür), gerisi kapalı
// Ayrıntılar'da. Faiz kartı yok: Tek bakış'ın 3. satırı aynı bilgi (Düzenle'den eklenebilir). Kendi düzenini kaydetmiş
// kullanıcı Ayrıntılı'da kalır (lib/ozetDuzen).
export const DEFAULT_LAYOUT = ["sade", "baslarken", "gelenler", "ayrinti", "kartlar", "donem-sonu", "uyarilar", "odemeler", "net", "zekat", "sadaka",
  "kumbaralar", "borc-plani", "butceler", "kategoriler", "isyerleri", "istatistik-link"];
/** Ayrıntılı (klasik) görünümün ilk düzeni: Özeti düzenle → Görünüm: Ayrıntılı (öncesinde saklı sıra yoksa). */
export const AYRINTILI_LAYOUT = ["kartlar", "faiz-ucret", "donem-sonu", "gelenler", "baslarken", "uyarilar", "net", "zekat", "sadaka", "butceler",
  "kategoriler", "isyerleri", "istatistik-link", "odemeler"];

export default function Dashboard() {
  const ui = useUI();
  const book = useBook().data; // ortak defter üyesi: kapalı bölümler gösterilmez
  const meta = useMeta().data; // kapatılan özellikler
  const demo = useDemo().data;
  const { ui: prefs, save: savePrefs } = useUiPrefs();
  const { month, setMonth } = ui;
  const summary = useQuery({ queryKey: ["summary", month], queryFn: () => api.summary(month) });
  const txns = useQuery({ queryKey: ["txns", month], queryFn: () => api.transactions(month) });
  const [sel, setSel] = useState<Sel>(null);
  const [insightFilter, setInsightFilter] = useState<Insight | null>(null);
  const [editBudgets, setEditBudgets] = useState(false);
  const [allInsights, setAllInsights] = useState(false);

  const detail = useMemo(() => {
    const rows = (txns.data ?? []).filter((r) => !r.pending && !r.skipped && !r.linked && r.kind !== "odeme" && !isEstimate(r));
    if (sel) {
      const pick = (r: Row) => (sel.by === "category" ? r.category : sel.by === "merchant" ? r.merchant : r.card);
      return rows.filter((r) => pick(r) === sel.name && r.kind !== "gelir");
    }
    const f = insightFilter?.filter;
    if (f) {
      return (txns.data ?? []).filter((r) => !isEstimate(r) &&
        (f.kind ? r.kind === f.kind : true) && (f.category ? r.category === f.category : true) &&
        (f.fx ? !!r.fx : true) && (f.pending ? !!r.pending : true) && (f.awaiting ? !!r.awaiting_statement : true));
    }
    return null;
  }, [sel, insightFilter, txns.data]);

  const s = summary.data;
  const select = (by: NonNullable<Sel>["by"]) => (name: string | null) => {
    setInsightFilter(null);
    setSel(name ? { by, name } : null);
  };
  const detailTitle = sel ? sel.name : insightFilter?.title;

  const [editing, setEditing] = useState(false);
  // sonradan eklenen bileşen (Dönem sonu) kayıtlı düzende de görünür; kaldırılınca işaretiyle saklanır (lib/ozetDuzen)
  const layout = ozetDuzeni(prefs.layout, (id) => !!WIDGETS[id], DEFAULT_LAYOUT);
  // ölçüm (yalnız toplam): Özet hangi görünümde açıldı; Sade'de SimpleDashboard gönderir
  const acilanGorunum = meta && !prefs.simple ? (sadeMi(layout) ? "minimal" : "ayrintili") : null;
  const caps = meta?.capabilities;
  useEffect(() => { if (acilanGorunum) gorunumAcildi(acilanGorunum, caps); }, [acilanGorunum, caps]);
  const sakli = sakliDuzen(prefs.layout); // öbür görünümün (Minimal ↔ Ayrıntılı) saklı sırası korunur
  const setLayout = (next: string[]) => savePrefs.mutate({ layout: kayitDuzeni(next, sakli) });
  // görünüm değiştir: öbürünün saklı sırası gelir, şimdiki saklanır; ölçüm yalnız toplam
  const gorunumSec = (hedef: Gorunum) => {
    const once: Gorunum = sadeMi(layout) ? "minimal" : "ayrintili";
    if (hedef === once) return;
    const n = gorunumDegistir(layout, sakli, hedef, (id) => !!WIDGETS[id], { minimal: DEFAULT_LAYOUT, ayrintili: AYRINTILI_LAYOUT });
    savePrefs.mutate({ layout: kayitDuzeni(n.layout, n.sakli) });
    gorunumDegisti(once, hedef, meta?.capabilities);
  };
  const current = month === (meta?.current_month ?? month);
  // sade düzen (Tek bakış + kapalı Ayrıntılar) yalnız içinde bulunulan dönemde; geçmiş dönemde bileşenler açık sırayla,
  // gelir/gider kartları en üstte (Harcanabilir ve "Bu ay ödenecek" bugünün rakamı)
  const sadeDuzen = sadeMi(layout);
  const sade = sadeDuzen && current;
  const gorunen = sadeDuzen && !current ? ["kartlar", ...layout.filter((id) => id !== "sade" && id !== "ayrinti" && id !== "kartlar")] : layout;
  const [acik, setAcik] = useState(ayrintiAcikMi);
  const toggleAcik = () => { ayrintiKaydet(!acik); setAcik(!acik); };
  // Tek bakış satırından Ayrıntılar'daki bileşene: kapalıysa açılır, sonra kayar
  const kaydir = (id: string) => {
    if (!acik) { ayrintiKaydet(true); setAcik(true); }
    setTimeout(() => {
      const el = document.getElementById(`w-${id}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      else ui.toast(`${WIDGETS[id]?.title ?? "Bu bölüm"} Özet'te kapalı; Düzenle'den ekleyebilirsin`);
    }, 60);
  };
  const move = (id: string, d: number) => {
    const next = [...layout];
    const i = next.indexOf(id);
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setLayout(next);
  };
  // bileşen, kapatılan özelliğe aitse ya da ortak defter üyesine kapalıysa gösterilmez
  const shown = (id: string) => {
    const w = WIDGETS[id];
    if (w?.cap && !meta?.capabilities?.includes(w.cap)) return false;
    return !w?.module || routeVisible(book, w.module, meta?.modules);
  };

  const detailBlock = detail && (
    <section className="card mt-4 overflow-hidden" id="detay">
      <div className="flex items-center justify-between border-b border-stone-100 px-3 py-2 dark:border-stone-800">
        <div>
          <h2 className="font-semibold">{detailTitle}</h2>
          <p className="text-xs text-stone-500">{detail.length} işlem · {tl(detailTotal(detail))}</p>
        </div>
        <button className="btn-ghost" onClick={() => { setSel(null); setInsightFilter(null); }}>Kapat</button>
      </div>
      <TxnList rows={[...detail].sort((a, b) => b.amount - a.amount)} />
    </section>
  );

  // sunucu tek tanım (a18ac2a): Artan = totals.left; ortak defterde faiz gizliyse kutu yok (eski sunucu: defter ayarı)
  const left = s ? s.totals.left ?? s.totals.net : 0;
  const kiyas = kiyasMetni(s?.vs_prev);
  const feesHidden = s?.totals.fees_hidden ?? !!book?.hide_fees;
  const render = (id: string): React.ReactNode => {
    if (!s) return null;
    switch (id) {
      // sade düzende gelir/gider kartları Ayrıntılar'da: büyük rakam yok (Tek bakış'ın 2. satırı), kartlar her boyda
      case "kartlar": return sade ? (<>
        <div className={cx("grid grid-cols-2 gap-2 sm:gap-3", feesHidden ? "lg:grid-cols-3" : "lg:grid-cols-4")}>{kpis}</div>
        <EstimateNote summary={s} />
        <TaksitSatiri summary={s} />
        <YearShortcut />
      </>) : (<>
        {/* telefon: tek büyük rakam + üç küçük; ayrıntılar masaüstündeki kartlarda */}
        <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-blue-600 via-blue-600 to-indigo-700 p-5 text-white shadow-lg shadow-blue-600/20 sm:hidden">
          <div aria-hidden className="pointer-events-none absolute -top-16 -right-10 size-48 rounded-full bg-white/10 blur-2xl" />
          <div aria-hidden className="pointer-events-none absolute -bottom-20 -left-10 size-40 rounded-full bg-emerald-300/20 blur-2xl" />
          <div className="relative text-sm text-blue-100">{month === meta?.current_month ? "Bu dönem harcadığın" : `${periodLabel(month)} döneminde harcadığın`}</div>
          <div className="relative mt-1 font-display text-[2.6rem] leading-none font-semibold tracking-tight tabular-nums">{tl(s.totals.expense)}</div>
          {kiyas && <div className="relative mt-1.5 text-sm text-blue-50" data-testid="kiyas">{kiyas.up ? "↑" : "↓"} {kiyas.text}</div>}
          {s.totals.savings > 0 && <div className="relative mt-1 text-xs text-blue-100">+ birikime {tl(s.totals.savings)} (gider sayılmaz)</div>}
          <div className="relative"><EstimateNote summary={s} tone="hero" /></div>
          <div className={cx("relative mt-5 grid gap-2 border-t border-white/15 pt-3", feesHidden ? "grid-cols-2" : "grid-cols-3")}>
            <div>
              <div className="text-[11px] tracking-wide text-blue-100 uppercase">Gelen</div>
              <div className="font-semibold tabular-nums">{tlCompact(s.totals.income)}</div>
              {s.pending.income > 0 && <div className="text-[11px] text-blue-100">+{tlCompact(s.pending.income)} bekliyor</div>}
              {(s.totals.interest_income ?? 0) > 0 && (
                <div className="text-[11px] leading-tight text-amber-200" data-testid="faiz-geliri">{faizGeliriNotu(tl(s.totals.interest_income!))}</div>)}
            </div>
            <div>
              <div className="text-[11px] tracking-wide text-blue-100 uppercase">{TERIM.artan.ad} <TerimBilgi id="artan" metin={s.definitions?.left} className="text-blue-100" /></div>
              <div className={cx("font-semibold tabular-nums", left < 0 && "text-rose-200")}>{tlCompact(left)}</div>
            </div>
            {!feesHidden && (
              <div>
                <div className="text-[11px] tracking-wide text-blue-100 uppercase">Faiz</div>
                <div className={cx("font-semibold tabular-nums", s.totals.fees > 0 && "text-amber-200")}>{tlCompact(s.totals.fees)}</div>
              </div>
            )}
          </div>
        </section>
        <div className={cx("hidden grid-cols-2 gap-2 sm:grid sm:gap-3", feesHidden ? "lg:grid-cols-3" : "lg:grid-cols-4")}>{kpis}</div>
        <div className="hidden sm:block"><EstimateNote summary={s} /></div>
        {/* harcamanın taksit kırılımı: harcamaya eklenmez (sunucu finance.installments_due) */}
        <TaksitSatiri summary={s} />
        {/* Aralık sonu / Ocak başı: "2026 yılın nasıl geçti?" → yıl özeti (yalnız içinde bulunulan dönemde) */}
        {month === meta?.current_month && <YearShortcut />}
      </>);
      case "sade": return current ? <TekBakis s={s} kiyas={kiyas} kaydir={kaydir} /> : null;
      // ayraç: düzenlerken çizgi, değilse bölüm başlığı ayrıca çizilir
      case "ayrinti": return editing ? (
        <div className="flex items-center gap-2 rounded-xl border border-dashed border-stone-300 px-3 py-2 text-xs text-stone-500 dark:border-stone-700">
          <ChevronDown size={14} /> Bunun altındakiler kapalı "Ayrıntılar" bölümünde</div>) : null;
      default: return renderDiger(id);
    }
  };
  const kpis: ReactNode = !s ? null : (<>
          <Kpi label="Gelir" value={s.totals.income} tone="green"
            sub={s.pending.income || (s.totals.interest_income ?? 0) > 0 ? <>
              {s.pending.income ? <span className="block">+{tl(s.pending.income)} bekliyor</span> : null}
              {(s.totals.interest_income ?? 0) > 0 && <span className="block text-amber-700 dark:text-amber-400">{faizGeliriNotu(tl(s.totals.interest_income!))}</span>}
            </> : undefined} />
          <Kpi label="Gider" value={s.totals.expense}
            sub={<>{`Kart ${tlCompact(s.totals.card_spend_incl_pending ?? s.totals.card_spend)} · Elle ${tlCompact(s.totals.manual_expense_excl_card_pending ?? s.totals.manual_expense)}${s.totals.savings ? ` · Birikime ${tlCompact(s.totals.savings)} ayrı` : ""}`}
              {kiyas && <span className={cx("block", kiyas.up ? "text-amber-700 dark:text-amber-400" : "text-green-700 dark:text-green-400")}>{kiyas.text}</span>}</>} />
          <Kpi label={<>{TERIM.artan.ad} (bu dönem) <TerimBilgi id="artan" metin={s.definitions?.left} /></>} value={left} tone={left < 0 ? "red" : "green"}
            sub={s.totals.debt_flow ? `borç/alacak hareketleri ayrı: ${signed(s.totals.debt_flow > 0 ? "+" : "", tl(s.totals.debt_flow))}` : TERIM.artan.kisa} />
          {!feesHidden && (
            // sade düzende (Ayrıntılar'daki kartlar) dönem bazlı olduğu açık yazılır; bu dönem 0 iken son ekstrelerde faiz
            // varsa küçük not (Tek bakış'ın 3. satırıyla çelişkili görünmesin)
            <Kpi label={sade ? "Bu dönem faiz" : "Faiz & ücret"} value={s.totals.fees} tone={s.totals.fees > 0 ? "red" : undefined}
              sub={sade && s.totals.fees <= 0 && s.interest_latest?.state === "var"
                ? <span data-testid="faiz-son-ekstre">son ekstrede {tl(s.interest_latest.total)}</span>
                : s.totals.refunds ? `İade ${tl(s.totals.refunds)}` : undefined} />)}
  </>);
  const renderDiger = (id: string): React.ReactNode => {
    if (!s) return null;
    switch (id) {
      // dönem sonu tahmini (Planlama'nın bu ayı): yalnız bu ve ileri dönemde (geçmişte expected null → kart yok)
      case "donem-sonu": return <DonemSonuKarti summary={s} />;
      // ortak defterde faiz gizliyse sunucu interest_paid null → kart yok
      case "faiz-ucret": return feesHidden ? null : <FaizOzetKarti summary={s} past={month < (meta?.current_month ?? month)} />;
      case "gelenler": return <SharedInbox />;
      // Başlarken (sahibin kurulumu) ve altında Yapılacaklar (belge hatırlatmaları; üyede görebildikleri)
      case "baslarken": return <>{!inSharedBook(book) && <Onboarding />}<Reminders /></>;
      case "uyarilar": return (
        <section>
          <h2 className="mb-2 font-display text-lg font-semibold tracking-tight">Dikkat etmen gerekenler</h2>
          <Insights items={allInsights ? s.insights : s.insights.slice(0, 3)} active={insightFilter} onFilter={(i) => {
            setSel(null);
            setInsightFilter(insightFilter?.title === i.title ? null : i);
          }} />
          {s.insights.length > 3 && (
            <button className="mt-2 flex w-full items-center justify-center gap-1 rounded-xl py-2 text-sm text-stone-600 active:bg-stone-100 dark:text-stone-400 dark:active:bg-stone-800"
              onClick={() => setAllInsights(!allInsights)}>
              {allInsights ? "Daha az göster" : `${s.insights.length - 3} uyarı daha`}
              <ChevronDown size={16} className={cx("transition-transform", allInsights && "rotate-180")} />
            </button>
          )}
          <HiddenInsights count={s.insights_hidden ?? 0} month={month} />
        </section>);
      case "net": return <NetWorth compact month={month} />;
      case "zekat": return <ZakatDue />;
      case "butceler": return (<>
        <BudgetList items={s.budgets} month={month} onEdit={() => setEditBudgets(true)}
          onSelect={(c) => { setInsightFilter(null); setSel({ by: "category", name: c }); }} />
        {editBudgets && <BudgetForm month={month} onClose={() => setEditBudgets(false)} onSaved={ui.toast} />}
      </>);
      // dönem başında boş: aynı "henüz harcama yok" yazısı iki kartta tekrarlanmasın
      case "kategoriler": return !s.by_category.length ? null : (
        <Section title="Kategoriye göre">
          <BarList items={s.by_category} selected={sel?.by === "category" ? sel.name : null} onSelect={select("category")}
            label={(i) => `${i.name} işlemlerini göster`} />
        </Section>);
      case "isyerleri": return !s.by_merchant.length ? null : (
        <Section title="En çok harcanan yerler">
          <BarList items={s.by_merchant} selected={sel?.by === "merchant" ? sel.name : null} onSelect={select("merchant")} limit={10}
            label={(i) => `${i.name} işlemlerini göster`} />
        </Section>);
      case "istatistik-link": return (
        <button className="card flex w-full items-center justify-between p-3 text-left text-sm active:bg-stone-50 dark:active:bg-stone-800" onClick={() => ui.go("istatistik")}>
          <span>📊 <b>İstatistik</b><span className="hidden sm:inline"> · geçen dönemlerle karşılaştırma, aylara göre gelir/gider, kartlar, faiz</span></span>
          <ChevronRight size={16} className="text-stone-400" />
        </button>);
      case "odemeler": return <PlanGate feature="odemeler" compact><Payables /></PlanGate>;
      case "trend": return s.trend.length ? <Section title="Aylara göre gelir ve gider"><TrendChart trend={s.trend} onPick={setMonth} /></Section> : null;
      case "karsilastirma": return <Compare month={month} />;
      case "yillik": return <YearSummary />;
      case "faiz": return book?.hide_fees ? null : <InterestHistory />;
      case "kumbaralar": return <Jars />;
      case "altin": return <Holdings />;
      case "borc-plani": return <DebtPlanCard />;
      // içinde bulunulan dönemde (geçmiş dönemde "son sadakan" anlamsız)
      case "sadaka": return month === meta?.current_month ? <SadakaOzetKarti /> : null;
      case "son-islemler": {
        const recent = (txns.data ?? []).filter((r) => !r.pending && !r.skipped && !r.linked && r.kind !== "odeme" && !isEstimate(r))
          .sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8);
        return recent.length ? <Section title="Son işlemler"><TxnList rows={recent} /></Section> : null;
      }
      default: return null;
    }
  };

  // bir bileşen (düzenlerken çerçevesi ve taşıma/kaldırma düğmeleriyle); görünmeyen ya da boş bileşen null
  const item = (id: string) => {
    const w = WIDGETS[id];
    if (!w || !shown(id)) return null;
    const body = render(id);
    if (!body) return null;
    const i = layout.indexOf(id);
    const kilit = sadeDuzen && id === "sade"; // Tek bakış hep en üstte
    const ilk = sadeDuzen ? 1 : 0; // sade düzende kimse Tek bakış'ın üstüne çıkamaz
    return (
      <div key={id} id={`w-${id}`} className={editing ? "relative rounded-xl ring-2 ring-blue-300 ring-offset-2 dark:ring-blue-800 dark:ring-offset-stone-950" : undefined}>
        {editing && (
          <div className="mb-1 flex items-center gap-1 text-xs">
            <span className="mr-auto font-medium text-blue-700 dark:text-blue-400">{w.title}{kilit ? " · hep en üstte" : ""}</span>
            {!kilit && <>
              <button className="btn-ghost p-1" aria-label="Yukarı" disabled={i <= ilk} onClick={() => move(id, -1)}><ArrowUp size={14} /></button>
              <button className="btn-ghost p-1" aria-label="Aşağı" disabled={i === layout.length - 1} onClick={() => move(id, 1)}><ArrowDown size={14} /></button>
              {id !== "ayrinti" && <button className="btn-ghost p-1 text-red-600" aria-label="Kaldır" onClick={() => setLayout(layout.filter((x) => x !== id))}><X size={14} /></button>}
            </>}
          </div>
        )}
        {body}
        {id === "uyarilar" && detailBlock}
      </div>
    );
  };
  // sade: ayracın üstü açık, altı (ve geçen ayın özeti, faturalar) kapalı Ayrıntılar'da
  const ayr = layout.indexOf("ayrinti");
  const ust = ayr < 0 ? layout : layout.slice(0, ayr);
  const alt = (ayr < 0 ? [] : layout.slice(ayr + 1)).map((id) => [id, item(id)] as const).filter(([, n]) => n);
  const sadeIcerik = (<>
    {ust.map(item)}
    <AyrintiBaslik open={acik} onToggle={toggleAcik} names={alt.map(([id]) => WIDGETS[id]!.title)} />
    {acik && <><MonthDigest /><BillsLine month={month} />{alt.map(([, n]) => n)}</>}
  </>);

  if (prefs.simple) return <SimpleDashboard />;
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="flex items-center gap-1 text-2xl font-semibold">Özet
          <button className="btn-ghost ml-1 p-2 font-sans text-xs font-normal" aria-label="Sade görünüm"
            title="Sade görünüm: büyük yazı, sadece önemli rakamlar" onClick={() => savePrefs.mutate({ simple: true })}>
            <Type size={16} /><span className="hidden sm:inline">Sade görünüm</span></button>
          <button className={cx("p-2 font-sans text-xs font-normal", editing ? "btn-primary" : "btn-ghost")} aria-label={editing ? "Bitti" : "Özeti düzenle"}
            title="Özeti düzenle: bileşenleri taşı, ekle, kaldır" onClick={() => setEditing(!editing)}>
            <Settings2 size={16} /><span className={editing ? "" : "hidden sm:inline"}>{editing ? "Bitti" : "Düzenle"}</span></button>
        </h1>
        <MonthPicker value={month} onChange={(m) => { setMonth(m); setSel(null); setInsightFilter(null); }} />
      </header>

      {meta && meta.modules_chosen === false && !inSharedBook(book) && !demo && <Features intro />}

      {!sade && <MonthDigest />}

      {!sade && <BillsLine month={month} />}


      <SorguDurumu q={summary} etiket="Özet yükleniyor">
        <div className="space-y-3"><KartIskelet satir={2} /><div className="grid grid-cols-2 gap-3"><KartIskelet satir={0} /><KartIskelet satir={0} /></div><SatirIskelet n={3} /></div>
      </SorguDurumu>

      {s && (
        <>
          {/* Özeti düzenle'nin en üstünde: görünüm (Minimal / Ayrıntılı); Sade (büyük yazı) Ayarlar → Görünüm'de */}
          {editing && (
            <section className="card space-y-1.5 p-3" data-testid="ozet-gorunum">
              <div className="text-xs font-medium tracking-wide text-stone-500 uppercase">Görünüm</div>
              <div className="w-full sm:w-72"><Segmented label="Görünüm" value={sadeMi(layout) ? "minimal" : "ayrintili"} onChange={(v) => gorunumSec(v as Gorunum)}
                options={[{ value: "minimal", label: GORUNUM_ADI.minimal }, { value: "ayrintili", label: GORUNUM_ADI.ayrintili }]} /></div>
              <p className="text-xs text-stone-500">{GORUNUM_ACIKLAMA[sadeMi(layout) ? "minimal" : "ayrintili"]}</p>
            </section>
          )}
          {sade && !editing && sadeIcerik}
          {(!sade || editing) && gorunen.map(item)}
          {!layout.includes("uyarilar") && detailBlock}
          {editing && (
            <section className="card space-y-2 p-3">
              <div className="text-sm font-medium">Bileşen ekle</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(WIDGETS).filter(([id]) => !layout.includes(id) && shown(id) && id !== "sade" && id !== "ayrinti").map(([id, w]) => (
                  <button key={id} className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                    onClick={() => setLayout([...layout, id])}><Plus size={13} /> {w.title}</button>
                ))}
                {Object.keys(WIDGETS).filter((id) => !layout.includes(id) && shown(id) && id !== "sade" && id !== "ayrinti").length === 0 && <span className="text-xs text-stone-500">Hepsi ekli.</span>}
              </div>
              <div className="flex gap-2">
                <button className="btn-primary" onClick={() => setEditing(false)}>Bitti</button>
                <button className="btn-ghost" onClick={() => { savePrefs.mutate({ reset_layout: true }); setEditing(false); }}>Varsayılana dön</button>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
