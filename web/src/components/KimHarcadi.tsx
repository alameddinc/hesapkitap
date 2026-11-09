import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Lock } from "lucide-react";
import { useState } from "react";
import { api, type Budget } from "../api";
import { call } from "../lib/apiError";
import { openTxns } from "../lib/txnFilter";
import { ek, tl, periodLabel } from "../format";
import { useUI } from "../ui";
import { useBook, usePeople } from "./hooks";
import { LockBadge, usePlan, useLock } from "./Plan";
import { barOf, KisiNokta, ORTAK, useSpenders } from "./Spender";

// Aile: "Kim ne harcadı" (İstatistik) ve ortak bütçede kişi başı kırılım (Özet → Bütçeler). Veri sunucudan:
// GET /api/book/member-spend?month (402 aile_rapor), /api/budgets status[].by_person. İstemci toplamaz, harcayanı kendisi
// bulmaz. Mobil defter/KimHarcadi.tsx ile aynı metinler; mobildeki ayrı "kişiler" sayfası burada kartın içinde açılır.

interface Person {
  key: string; user_id: number | null; name: string; owner: boolean; me: boolean; amount: number; count: number; pct: number;
  categories: { name: string; amount: number; count: number }[];
}
interface MemberSpend { month: string; shared: boolean; total: number; people: Person[]; me: number | null }


const adOf = (p: Pick<Person, "name" | "me">) => (p.me ? "Sen" : p.name);

/** O kişinin bu dönemdeki giderleri (İşlemler süzgeci; dönem ay seçicide) */
const openPerson = (key: string, category?: string) => openTxns({ kim: key, gider: true, kategori: category });

/** İstatistik: dönemin gideri kişi başına; kişiye dokununca işlemleri, "Kategorilere göre" ile kırılım. */
export function KimHarcadi({ month }: { month: string }) {
  const plan = usePlan();
  const lock = useLock("aile_rapor");
  const pp = usePeople();
  const s = useSpenders();
  const ui = useUI();
  const [detail, setDetail] = useState(false);
  const book = useBook().data;
  const q = useQuery({ queryKey: ["member-spend", month], enabled: !lock && pp.multi, retry: false,
    queryFn: () => call<MemberSpend>("GET", `/api/book/member-spend?month=${month}`) });
  if (lock) {
    if (!plan?.hosted) return null;
    return (
      <section className="card flex items-start gap-3 p-3 text-sm" data-testid="kim-harcadi-kilit">
        <Lock size={18} className="mt-0.5 shrink-0 text-amber-600" aria-hidden />
        <div className="min-w-0 space-y-0.5">
          <h2 className="font-medium">Kim ne harcadı Aile paketinde <LockBadge need={lock.need} className="ml-1 align-middle" /></h2>
          <p className="text-stone-600 dark:text-stone-400">{lock.desc || "Ortak defterde kişi başı harcama ve ortak bütçede kim ne kadar harcadı."}</p>
        </div>
      </section>
    );
  }
  if (!pp.multi) {
    // tek kişi: üyenin kendi defteri ya da Aile paketinde henüz üyesi olmayan defter → neden boş olduğunu söyle
    const mine = !!book?.member && book.book === "kendi";
    if (!mine && !plan?.hosted) return null;
    return (
      <section className="card space-y-1 p-3 sm:p-4" data-testid="kim-harcadi-bos">
        <h2 className="font-semibold">{mine ? "Benim defterinde kişi başı rapor yok" : "Defterde henüz başka kimse yok"}</h2>
        <p className="text-sm text-stone-600 dark:text-stone-400">{mine
          ? "Bu defter yalnız senin; ortak deftere geçince kim ne harcadı burada görünür."
          : "Ortak deftere aileni davet edince kim ne harcadı burada görünür."}</p>
        {!mine && <button className="text-sm font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => ui.go("ayarlar")}>Aileni davet et</button>}
      </section>
    );
  }
  const d = q.data;
  const list = d?.people ?? [];
  const max = Math.max(1, ...list.map((p) => p.amount));
  const top = list.find((p) => p.key !== ORTAK);
  const pct = top ? Math.round(top.pct) : 0;
  const say = !d ? "" : !list.length ? "Bu dönemde gider yok."
    : !top ? "Bu dönemin harcamaları ekstre ve dökümden; kimin yaptığı belli değil."
    : `${top.me ? "En çok sen harcadın" : `En çok ${top.name} harcadı`}: giderin %${pct}'${ek(pct)}.`;
  const go = (key: string, category?: string) => { ui.setMonth(month); openPerson(key, category); };
  return (
    <section className="card space-y-2 p-3 sm:p-4" data-testid="kim-harcadi">
      <h2 className="font-semibold">Kim ne harcadı · {periodLabel(month)}</h2>
      {q.isLoading && <p className="text-sm text-stone-500">Yükleniyor…</p>}
      {q.error && <p className="text-sm text-red-600" role="alert">{(q.error as Error).message}</p>}
      {d && <p className="text-sm text-stone-600 dark:text-stone-400">{say}</p>}
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {list.map((p) => (
          <li key={p.key} className="py-1.5">
            <button className="flex w-full items-center gap-3 rounded-lg py-1 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
              onClick={() => go(p.key)} data-testid={`kim-${p.key}`}
              aria-label={`${adOf(p)}: ${tl(p.amount)}, yüzde ${Math.round(p.pct)}, ${p.count} işlem. İşlemleri gör`}>
              <KisiNokta k={p.key} people={s.people} size={28} />
              <span className="min-w-0 flex-1 space-y-1">
                <span className="flex justify-between gap-2 text-sm">
                  <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{adOf(p)}</span>
                  <span className="shrink-0 text-stone-600 tabular-nums dark:text-stone-300">{tl(p.amount)} · %{Math.round(p.pct)}</span>
                </span>
                <span className="block h-2 rounded-full bg-stone-100 dark:bg-stone-800">
                  <span className={`block h-2 rounded-full ${barOf(s.people, p.key)}`} style={{ width: `${Math.max(2, Math.round((Math.max(0, p.amount) / max) * 100))}%` }} />
                </span>
              </span>
              <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
            </button>
            {detail && (
              <ul className="mt-1 ml-10 space-y-0.5">
                {p.categories.slice(0, 6).map((c) => (
                  <li key={c.name}>
                    <button className="flex w-full justify-between gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => go(p.key, c.name)}>
                      <span className="min-w-0 [overflow-wrap:anywhere]">{c.name}</span>
                      <span className="shrink-0 text-stone-500 tabular-nums">{tl(c.amount)}</span>
                    </button>
                  </li>
                ))}
                {p.categories.length > 6 && (
                  <li><button className="px-1 text-xs text-blue-700 hover:underline dark:text-blue-400" onClick={() => go(p.key)}>
                    Tüm işlemler ({p.categories.length} kategori)</button></li>
                )}
                {/* Ortak'taki kart ekstresi: kart kişiye bağlanınca o kişiye yazılır (Hesaplar → Kimin kartı) */}
                {p.key === ORTAK && p.amount > 0 && s.edit && (
                  <li><button className="px-1 text-left text-xs font-medium text-blue-700 hover:underline dark:text-blue-400" data-testid="ortak-karta-bagla"
                    onClick={() => ui.go("hesaplar")}>Ortak harcamaları kişiye yazmak için kartı kişiye bağla</button></li>
                )}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {list.some((p) => p.key === ORTAK) && (
        <p className="text-xs text-stone-500">Ortak: ekstre ve dökümden gelen, kimin harcadığı belli olmayan kalemler.</p>
      )}
      {list.length > 0 && (
        <button className="text-sm font-medium text-blue-700 hover:underline dark:text-blue-400" aria-expanded={detail}
          onClick={() => setDetail(!detail)}>{detail ? "Kategorileri gizle" : "Kategorilere göre"}</button>
      )}
    </section>
  );
}

/** Ortak bütçede kim ne kadar: /api/budgets status[].by_person (yalnız ortak defterde ve Aile paketinde dolu). */
export function useBudgetPeople(month: string | undefined) {
  const pp = usePeople();
  const q = useQuery({ queryKey: ["budgets", month], enabled: !!month && pp.multi, queryFn: () => api.budgets(month!) });
  return new Map((q.data?.status ?? []).map((b: Budget) => [b.category, b.by_person ?? []]));
}

/** Bütçe satırının altında: ince yığılmış çubuk ve "Sen ₺300 · Ayşe ₺200 · Ortak ₺80". Tek kişilik defterde yok. */
export function BudgetPeople({ items }: { items?: Budget["by_person"] }) {
  const s = useSpenders();
  if (!items?.length) return null;
  const total = items.reduce((a, x) => a + Math.max(0, x.amount), 0); // parity-ok: SUNUM — çubuk genişliği oranı
  if (total <= 0) return null;
  const ad = (x: { key: string; name: string }) => (x.key !== ORTAK && s.adOf(x.key) === "Sen" ? "Sen" : x.name);
  return (
    <span className="mt-1 block space-y-1">
      <span role="img" aria-label={`Kim harcadı: ${items.map((x) => `${ad(x)} ${tl(x.amount)}`).join(", ")}`}
        className="flex h-1 gap-0.5 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
        {items.map((x) => <span key={x.key} className={barOf(s.people, x.key)} style={{ width: `${(Math.max(0, x.amount) / total) * 100}%` }} />)}
      </span>
      <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-stone-500">
        {items.map((x) => (
          <span key={x.key} className="inline-flex items-center gap-1">
            <KisiNokta k={x.key} people={s.people} size={14} /><span>{ad(x)}</span>
            <b className="font-semibold text-stone-700 tabular-nums dark:text-stone-200">{tl(x.amount)}</b>
          </span>
        ))}
      </span>
    </span>
  );
}
