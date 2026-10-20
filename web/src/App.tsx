import { useQuery } from "@tanstack/react-query";
import { CalendarRange, Eye, EyeOff, LayoutDashboard, List, Menu, PiggyBank, Plus, ReceiptText, Settings2, Sheet, Tags, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type BillDraft, type Payable, type Row } from "./api";
import AddSheet from "./components/AddSheet";
import BillForm from "./components/BillForm";
import PayForm from "./components/PayForm";
import RecurringMonthForm, { type RecurringMonth } from "./components/RecurringMonthForm";
import { useMeta } from "./components/hooks";
import CardTxnForm from "./components/CardTxnForm";
import EntryForm from "./components/EntryForm";
import { cx, setAmountsHidden } from "./format";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Setup from "./pages/Setup";
import More, { LogoutButton } from "./pages/More";
import Planning from "./pages/Planning";
import Rules from "./pages/Rules";
import Savings from "./pages/Savings";
import Table from "./pages/Table";
import Settings from "./pages/Settings";
import Statements from "./pages/Statements";
import Transactions from "./pages/Transactions";
import { UIContext, type EntryDraft, type Route, type UI } from "./ui";

const PAGES: Record<Route, () => React.ReactElement> = {
  ozet: Dashboard,
  islemler: Transactions,
  plan: Planning,
  ekstreler: Statements,
  kurallar: Rules,
  ayarlar: Settings,
  tasarruf: Savings,
  tablo: Table,
  diger: More,
};

const NAV: { route: Route; label: string; icon: LucideIcon }[] = [
  { route: "ozet", label: "Özet", icon: LayoutDashboard },
  { route: "islemler", label: "İşlemler", icon: List },
  { route: "tablo", label: "Tablo", icon: Sheet },
  { route: "plan", label: "Planlama", icon: CalendarRange },
  { route: "tasarruf", label: "Tasarruf", icon: PiggyBank },
  { route: "ekstreler", label: "Ekstreler", icon: ReceiptText },
  { route: "kurallar", label: "Kurallar", icon: Tags },
  { route: "ayarlar", label: "Ayarlar", icon: Settings2 },
];

const nav = (r: Route) => NAV.find((n) => n.route === r)!;

const readRoute = (): Route => {
  const r = window.location.hash.replace(/^#\/?/, "").split("?")[0] as Route;
  return r in PAGES ? r : "ozet";
};

type ModalState =
  | { type: "add" }
  | { type: "entry"; row?: Row; draft?: EntryDraft }
  | { type: "card"; row: Row }
  | { type: "recurring"; id?: number }
  | { type: "bill"; draft: BillDraft }
  | { type: "pay"; p: Payable }
  | { type: "rmonth"; r: RecurringMonth }
  | null;

export default function App() {
  const auth = useQuery({
    queryKey: ["auth"],
    queryFn: () => fetch("/api/auth/status").then((r) => r.json() as Promise<{
      required: boolean; authenticated: boolean; setup_required?: boolean; setup_code_needed?: boolean;
    }>),
  });
  if (auth.isLoading) return null;
  if (auth.data?.setup_required) return <Setup codeNeeded={!!auth.data.setup_code_needed} />;
  if (auth.data && !auth.data.authenticated) return <Login />;
  return <Shell />;
}

// Açılışta tutarlar gizli (cihaz ayarı); göz ikonuyla bu oturum için açılır
const hideOnOpen = () => { try { return localStorage.getItem("hk-hide-on-open") !== "0"; } catch { return true; } };
const sessionRevealed = () => { try { return sessionStorage.getItem("hk-revealed") === "1"; } catch { return false; } };

function Shell() {
  const [hidden, setHidden] = useState(() => hideOnOpen() && !sessionRevealed());
  setAmountsHidden(hidden);
  const toggleHidden = () => {
    const next = !hidden;
    try { sessionStorage.setItem("hk-revealed", next ? "0" : "1"); } catch { /* özel mod */ }
    setHidden(next);
  };
  const EyeBtn = ({ className }: { className?: string }) => (
    <button onClick={toggleHidden} aria-label={hidden ? "Tutarları göster" : "Tutarları gizle"} aria-pressed={!hidden}
      className={cx("btn-ghost p-2", className)}>
      {hidden ? <EyeOff size={18} /> : <Eye size={18} />}
    </button>
  );
  const [route, setRoute] = useState<Route>(readRoute);
  const meta = useMeta();
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  // açılışta içinde bulunulan dönem (ay dönümü ayarına göre; ör. 19 Ekim → Kasım dönemi)
  const [monthInit, setMonthInit] = useState(false);
  useEffect(() => {
    if (!monthInit && meta.data?.current_month) {
      setMonth(meta.data.current_month);
      setMonthInit(true);
    }
  }, [meta.data, monthInit]);
  const [modal, setModal] = useState<ModalState>(null);
  const [toastMsg, setToast] = useState<string | null>(null);
  const recurring = useQuery({ queryKey: ["recurring"], queryFn: api.recurring, enabled: modal?.type === "recurring" });

  // Ana ekran kısayolu: #/islemler?ekle=1 → Ekle panelini aç
  useEffect(() => {
    if (window.location.hash.includes("ekle=1")) {
      setModal({ type: "add" });
      window.history.replaceState(null, "", window.location.hash.replace(/\?ekle=1/, ""));
    }
  }, []);
  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!toastMsg) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toastMsg]);

  const close = useCallback(() => setModal(null), []);
  const ui: UI = useMemo(() => ({
    month,
    setMonth,
    go: (r) => {
      window.location.hash = `/${r}`;
      window.scrollTo(0, 0);
    },
    openAdd: () => setModal({ type: "add" }),
    openEntry: (row, draft) => setModal({ type: "entry", row, draft }),
    openCardTxn: (row) => setModal({ type: "card", row }),
    openRecurring: (id) => setModal({ type: "recurring", id }),
    openBill: (draft) => setModal({ type: "bill", draft: draft ?? {} }),
    openPay: (p) => setModal({ type: "pay", p }),
    openRecurringMonth: (r) => setModal({ type: "rmonth", r }),
    toast: setToast,
  }), [month]);

  const Page = PAGES[route];
  const rec = modal?.type === "recurring" ? recurring.data?.find((r) => r.id === modal.id) : undefined;

  return (
    <UIContext.Provider value={ui}>
      <div className="md:flex">
        {/* Masaüstü yan menü */}
        <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-stone-200 bg-white p-3 md:flex dark:border-stone-800 dark:bg-stone-900">
          <div className="mb-4 flex items-center gap-2 px-2 py-1 text-lg font-semibold">
            <img src="/icons/logo-mark.png" alt="" className="size-7" /> <span className="flex-1">HesapKitap</span>
            <EyeBtn className="-mr-2" />
          </div>
          <button className="btn-primary mb-3 justify-center py-2" onClick={ui.openAdd}><Plus size={16} /> Ekle</button>
          <nav className="space-y-0.5">
            {NAV.map((n) => (
              <a key={n.route} href={`#/${n.route}`}
                className={cx("flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm",
                  route === n.route ? "bg-stone-100 font-medium dark:bg-stone-800" : "text-stone-600 hover:bg-stone-50 dark:text-stone-400 dark:hover:bg-stone-800/50")}>
                <n.icon size={16} /> {n.label}
              </a>
            ))}
          </nav>
          <div className="mt-auto"><LogoutButton /></div>
        </aside>

        {/* mobil üst çubuk: logo + tutarları göster/gizle */}
        <div className="sticky top-0 z-30 flex items-center gap-2 border-b border-stone-200 bg-white/90 px-4 pt-[env(safe-area-inset-top)] backdrop-blur md:hidden dark:border-stone-800 dark:bg-stone-900/90">
          <img src="/icons/logo-mark.png" alt="" className="size-6" />
          <span className="flex-1 py-2 font-semibold">HesapKitap</span>
          <EyeBtn className="-mr-2" />
        </div>
        <main className={cx("mx-auto w-full min-w-0 px-4 pt-4 pb-28 md:px-6 md:pt-6 md:pb-10", route === "tablo" ? "max-w-none" : "max-w-5xl")}>
          <Page />
        </main>
      </div>

      {/* Mobil alt menü */}
      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-stone-200 bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden dark:border-stone-800 dark:bg-stone-900/95">
        <div className="grid grid-cols-5 items-end">
          {[nav("ozet"), nav("islemler"), null, nav("plan"), { route: "diger" as Route, label: "Diğer", icon: Menu }].map((n, i) =>
            n ? (
              <a key={n.route} href={`#/${n.route}`}
                className={cx("flex flex-col items-center gap-0.5 py-2 text-[11px]",
                  route === n.route || (n.route === "diger" && ["ekstreler", "kurallar", "ayarlar", "tasarruf", "tablo"].includes(route))
                    ? "text-blue-600" : "text-stone-500")}>
                <n.icon size={20} /> {n.label}
              </a>
            ) : (
              <div key={i} className="flex justify-center">
                <button onClick={ui.openAdd} aria-label="Ekle"
                  className="-mt-5 flex size-14 items-center justify-center rounded-full bg-blue-600 text-white shadow-lg active:scale-95">
                  <Plus size={26} />
                </button>
              </div>
            ),
          )}
        </div>
      </nav>

      {modal?.type === "add" && (
        <AddSheet onClose={close} onPick={(draft) => setModal({ type: "entry", draft })}
          onBill={(draft) => setModal({ type: "bill", draft })} />
      )}
      {modal?.type === "entry" && (
        <EntryForm row={modal.row} draft={modal.draft} onClose={close} onSaved={setToast} />
      )}
      {modal?.type === "card" && <CardTxnForm row={modal.row} onClose={close} onSaved={setToast} />}
      {modal?.type === "recurring" && rec && <EntryForm recurring={rec} onClose={close} onSaved={setToast} />}
      {modal?.type === "bill" && <BillForm draft={modal.draft} onClose={close} onSaved={setToast} />}
      {modal?.type === "pay" && <PayForm p={modal.p} onClose={close} onSaved={setToast} />}
      {modal?.type === "rmonth" && <RecurringMonthForm r={modal.r} onClose={close} />}

      {toastMsg && (
        <div className="fixed inset-x-0 bottom-24 z-[60] flex justify-center px-4 md:bottom-6" role="status">
          <div className="rounded-full bg-stone-900 px-4 py-2 text-sm text-white shadow-lg dark:bg-stone-100 dark:text-stone-900">{toastMsg}</div>
        </div>
      )}
    </UIContext.Provider>
  );
}
