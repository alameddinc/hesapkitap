import { useQuery } from "@tanstack/react-query";
import { TERIM } from "../lib/terimler";
import TerimBilgi from "./TerimBilgi";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { AlertTriangle, ChevronDown, ChevronRight, CreditCard, GitMerge, Landmark, Plus, Trash2, Wallet } from "lucide-react";
import { useState } from "react";
import type { CardCycle } from "../api";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { isLimited, useBook, useMeta, useSave } from "./hooks";
import AccountMerge from "./AccountMerge";
import Wallets from "./Wallets";
import CycleLine from "./CardCycle";

interface Account { id: number; name: string; kind: "banka" | "nakit" | "diger"; balance: number; as_of: string | null; source: "dokum" | "elle" }
interface NW {
  reserved: number; free: number; spendable: number; holdings: number; family_holdings: number; zakat_due: number;
  accounts: (Account & { reserved: number })[]; cash: number; cards: { title: string; amount: number; due_date: string; estimated: boolean }[];
  card_debt: number; card_estimated: number; receivable: number; payable: number; net: number; net_after_estimates: number;
  wallets: { id: number; name: string; balance: number }[]; in_wallets: number; unknown_banks: string[];
  credit_cards: { title: string; last_period_end: string; last_total: number; last_due: string | null; paid_at: string | null;
    unpaid: number; next_estimate: number | null; next_due: string | null; cycle: CardCycle | null }[];
  stale: string[];
  /** sunucu toplamları (istemci toplamaz); card_overdue: ödenmemiş eski ekstreler, kalanı son ekstrede (toplamda yok) */
  totals?: { card_debt: number; card_estimated: number; card_overdue: number; cash: number; spendable: number; net: number };
  card_overdue?: { title: string; amount: number; due_date: string; statement_id: number; rolled_into: number | null }[];
  /** sunucu a18ac2a: Harcanabilir'in parçaları ve açıklaması (Özet ve Hesaplar'da aynı metin) */
  spendable_parts?: { key: string; label: string; amount: number; sign: "+" | "−" }[];
  spendable_explain?: string;
}

/** Sunucu totals'ı varsa o, yoksa (eski sunucu) üst düzey alanlar. */
const tot = (d: NW) => d.totals ?? { card_debt: d.card_debt, card_estimated: d.card_estimated, card_overdue: 0, cash: d.cash, spendable: d.spendable, net: d.net };

/** Gecikmiş kart ekstresi uyarısı: ödenmemiş eski ekstre (kalanı son ekstreye devretti; toplamlara ayrıca girmez). */
function CardOverdue({ d, compact }: { d: NW; compact?: boolean }) {
  const list = d.card_overdue ?? [];
  if (!list.length) return null;
  return (
    <span className={cx("flex gap-1.5 rounded-lg bg-red-50 p-2 text-xs text-red-800 dark:bg-red-950/40 dark:text-red-300", compact ? "mt-1.5" : "")} role="note">
      <AlertTriangle size={14} className="mt-px shrink-0" aria-hidden />
      <span>
        <b>Gecikmiş kart ödemesi:</b>{" "}
        {list.map((o) => `${o.title} (son ödeme ${dateTR(o.due_date)}, ${tl(o.amount)})`).join(" · ")}.{" "}
        Kalanı sonraki ekstreye devretti ve kart borcunda zaten sayılıyor; gecikme faizi işler.
      </span>
    </span>
  );
}

const json = <T,>(method: string, url: string, body?: unknown) => call<T>(method, url, body);

/** Gerçek durum: hesaplardaki para (dökümden ya da elle) + kartlar − kart borcu ± borç/alacak.
 *  compact: Özet'teki tek satırlık özet (dokununca Hesaplar sayfası); değilse sayfanın tamamı. */
/** Özet: ödenmemiş geçmiş yıl zekâtı varsa hatırlat (Zekât sayfasına götürür). */
export function ZakatDue() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["net-worth"], queryFn: () => json<NW>("GET", "/api/net-worth") });
  if (!q.data?.zakat_due) return null;
  return (
    <button className="card flex w-full items-center gap-3 border-amber-300 bg-amber-50 p-3 text-left text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
      onClick={() => ui.go("zekat")}>
      <span className="flex-1">🌙 Geçmiş yıllardan ödenmemiş görünen zekâtın var: <b>{tl(q.data.zakat_due)}</b>. Ödediysen ilgili yıla "Ödedim" de.</span>
      <ChevronRight size={16} className="shrink-0" />
    </button>
  );
}

interface NWAt { as_of: string; cash: number; holdings: number; card_debt: number; receivable: number; payable: number; net: number;
  unknown_accounts: string[]; prices: boolean }

/** Geçmiş dönem seçiliyse: o dönemin son günündeki net durum (yaklaşık, dökümlerden geriye hesap). */
function NetWorthAt({ month }: { month: string }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["net-worth-at", month], queryFn: () => json<NWAt>("GET", `/api/net-worth/at?month=${month}`) });
  const d = q.data;
  return (
    <button className="card flex w-full items-center gap-3 p-4 text-left" onClick={() => ui.go("hesaplar")}>
      <Landmark size={18} className="shrink-0 text-stone-500" />
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-stone-500">Net durum · {d ? dateTR(d.as_of) : "…"} itibarıyla (dönem sonu, yaklaşık)</span>
        {d && <>
          <span className={cx("block text-lg font-semibold tabular-nums", d.net < 0 ? "text-red-600" : "text-green-700 dark:text-green-400")}>{tl(d.net)}</span>
          <span className="block text-xs text-stone-500">
            Bankada {tl(d.cash)}{d.holdings ? ` · altın/döviz ${tl(d.holdings)}` : ""}{d.card_debt ? ` · kart borcu −${tl(d.card_debt)}` : ""}
            {d.receivable ? ` · alacak +${tl(d.receivable)}` : ""}{d.payable ? ` · borç −${tl(d.payable)}` : ""}
          </span>
          {d.unknown_accounts.length > 0 && <span className="block text-xs text-amber-600">O tarihte dökümü olmayan: {d.unknown_accounts.join(", ")} (sayılmadı)</span>}
        </>}
      </span>
      <ChevronRight size={16} className="shrink-0 text-stone-400" />
    </button>
  );
}

/** Harcanabilir'in parçaları (sunucu spendable_parts): (i) penceresinde bugünkü rakamlarla. */
function SpendableParts({ d }: { d: NW }) {
  const parts = (d.spendable_parts ?? []).filter((p) => p.amount);
  if (!parts.length) return null;
  return (
    <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
      {parts.map((p) => (
        <li key={p.key} className="flex justify-between gap-3 px-3 py-1.5">
          <span>{p.label}</span><span className="shrink-0 tabular-nums">{p.sign === "−" ? "−" : "+"}{tl(p.amount)}</span>
        </li>
      ))}
      <li className="flex justify-between gap-3 px-3 py-1.5 font-semibold">
        <span>{TERIM.harcanabilir.ad}</span><span className="shrink-0 tabular-nums">{tl(tot(d).spendable)}</span>
      </li>
    </ul>
  );
}

/** İki rakam: harcanabilir (ödenecekler ve kumbara düşülmüş, altın/döviz hariç) ve net durum (her şey dahil).
 *  Açıklamalar (i) penceresinde (title değil: dokunmatikte görünmez); eksi harcanabilir neden eksi, alt satırda. */
function Headline({ d }: { d: NW }) {
  const tone = (x: number) => (x < 0 ? "text-red-600" : "text-green-700 dark:text-green-400");
  const sp = tot(d).spendable;
  return (
    <span className="mb-1 grid grid-cols-2 gap-3">
      <span>
        <span className="block text-xs text-stone-500">{TERIM.harcanabilir.ad} <TerimBilgi id="harcanabilir" metin={d.spendable_explain} ek={<SpendableParts d={d} />} /></span>
        <span className={cx("block text-lg font-semibold tabular-nums", tone(sp))}>{tl(sp)}</span>
        <span className={cx("block text-xs", sp < 0 ? "text-red-600 dark:text-red-400" : "text-stone-500")}>
          {sp < 0 ? `Ödeyeceklerin eldeki paradan ${tl(-sp)} fazla` : TERIM.harcanabilir.kisa}</span>
      </span>
      <span>
        <span className="block text-xs text-stone-500">{TERIM.netDurum.ad}{d.holdings ? " (altın/döviz dahil)" : ""} <TerimBilgi id="netDurum" /></span>
        <span className={cx("block text-lg font-semibold tabular-nums", tone(tot(d).net))}>{tl(tot(d).net)}</span>
        <span className="block text-xs text-stone-500">{TERIM.netDurum.kisa}</span>
      </span>
    </span>
  );
}

export default function NetWorth({ compact = false, month }: { compact?: boolean; month?: string }) {
  const ui = useUI();
  const meta = useMeta();
  const q = useQuery({ queryKey: ["net-worth"], queryFn: () => json<NW>("GET", "/api/net-worth") });
  const [open, setOpen] = useState(!compact);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [bal, setBal] = useState("");
  const [kind, setKind] = useState<"banka" | "nakit" | "diger">("banka");
  const [merging, setMerging] = useState<Account | null>(null); // "Başka hesapla birleştir"
  const canMerge = !isLimited(useBook().data); // sahip ya da tam yetkili üye
  const add = useSave(() => json("POST", "/api/accounts", { name, kind, balance: parseAmount(bal) }),
    () => { ui.toast("Hesap eklendi"); setAdding(false); setBal(""); });
  const addKnown = useSave((a: { name: string; balance: number }) => json("POST", "/api/accounts", { name: a.name, kind: "banka", balance: a.balance }),
    () => ui.toast("Bakiye kaydedildi"));
  const edit = useSave(({ id, balance }: { id: number; balance: number }) => json("PATCH", `/api/accounts/${id}`, { balance }),
    () => ui.toast("Bakiye güncellendi"));
  const del = useSave((id: number) => json("DELETE", `/api/accounts/${id}`), () => ui.toast("Silindi"));
  const d = q.data;
  if (compact && month && meta.data?.current_month && month < meta.data.current_month) return <NetWorthAt month={month} />;
  if (!d) return null;
  const empty = !d.accounts.length && !d.holdings && !d.wallets.length && !d.credit_cards.length && !d.unknown_banks.length;
  if (compact) {
    return (
      <div className="card relative flex w-full items-center gap-3 p-4 text-left">
        {/* kartın tamamı Hesaplar'a gider; (i) düğmeleri iç içe düğme olmasın diye kardeş ve üstte */}
        <button className="absolute inset-0 rounded-[inherit]" aria-label="Hesaplar ve net durum ayrıntısı" onClick={() => ui.go("hesaplar")} />
        <Landmark size={18} className="pointer-events-none relative shrink-0 text-stone-500" />
        {empty ? (
          <span className="pointer-events-none relative flex-1 text-sm text-stone-500">Hesaplarını, nakdini ve yemek/hediye kartlarını ekle: gerçek durumunu görürsün.</span>
        ) : (
          <span className="pointer-events-none relative min-w-0 flex-1">
            <Headline d={d} />
            {/* döküm telefonda gizli: karta dokununca Hesaplar'da tamamı */}
            <span className="hidden text-xs text-stone-500 sm:block">
              Bankada {tl(tot(d).cash)}{d.holdings ? ` · altın/döviz ${tl(d.holdings)}` : ""}{d.in_wallets ? ` · kartlarda ${tl(d.in_wallets)}` : ""}{tot(d).card_debt ? ` · kart borcu −${tl(tot(d).card_debt)}` : ""}
              {d.receivable ? ` · alacak +${tl(d.receivable)}` : ""}{d.payable ? ` · borç −${tl(d.payable)}` : ""}
              {d.zakat_due ? ` · zekât borcu −${tl(d.zakat_due)}` : ""}
            </span>
            {d.reserved > 0 && <span className="block text-xs text-stone-500">Kumbaralarda ayrılan {tl(d.reserved)}</span>}
            {d.unknown_banks.length > 0 && (
              <span className="block text-xs text-amber-600">Bakiyesi girilmemiş: {d.unknown_banks.join(", ")}</span>
            )}
            <CardOverdue d={d} compact />
          </span>
        )}
        <ChevronRight size={16} className="pointer-events-none relative shrink-0 text-stone-400" />
      </div>
    );
  }
  return (
    <section className="card">
      <div className="relative flex w-full items-center gap-3 p-4 text-left">
        <button className="absolute inset-0 rounded-t-[inherit]" aria-expanded={open} aria-label={open ? "Hesap ayrıntısını gizle" : "Hesap ayrıntısını göster"}
          onClick={() => setOpen(!open)} />
        <Landmark size={18} className="pointer-events-none relative shrink-0 text-stone-500" />
        <div className="pointer-events-none relative min-w-0 flex-1">
          <Headline d={d} />
          <div className="text-xs text-stone-500">
            Bankada {tl(tot(d).cash)}{d.holdings ? ` · altın/döviz ${tl(d.holdings)}` : ""}{d.in_wallets ? ` · kartlarda ${tl(d.in_wallets)}` : ""}{tot(d).card_debt ? ` · kart borcu ${tl(tot(d).card_debt)}` : ""}
            {tot(d).card_estimated ? ` · kesilecek ekstreler tahmini ~${tl(tot(d).card_estimated)}` : ""}
          </div>
          {d.reserved > 0 && <div className="text-xs text-stone-500">Kumbaralarda ayrılan {tl(d.reserved)}</div>}
          <CardOverdue d={d} />
        </div>
        <ChevronDown size={16} className={cx("pointer-events-none relative text-stone-400 transition-transform", open && "rotate-180")} />
      </div>
      {open && (
        <div className="space-y-3 border-t border-stone-100 px-4 pt-3 pb-4 dark:border-stone-800">
          <div>
            <div className="mb-1 text-xs font-medium text-stone-500">Banka ve nakit</div>
            <ul className="space-y-1.5 text-sm">
              {d.accounts.map((a) => (
                <li key={a.id} className="flex items-center gap-2">
                  {a.kind === "banka" ? <Landmark size={14} className="text-stone-400" /> : <Wallet size={14} className="text-stone-400" />}
                  <span className="flex-1">
                    {a.name}
                    <span className={cx("ml-1 text-xs", d.stale.includes(a.name) ? "text-amber-600" : "text-stone-500")}>
                      {a.as_of ? dateTR(a.as_of) : ""}{a.source === "dokum" ? " · dökümden" : " · elle"}
                      {d.stale.includes(a.name) && " · eski, yeni döküm yükle"}
                      {a.reserved > 0 && ` · ${tl(a.reserved)} kumbarada`}
                    </span>
                  </span>
                  <button className="tabular-nums underline decoration-dotted" title="Bakiyeyi düzelt"
                    onClick={() => {
                      const v = window.prompt(`${a.name} bakiyesi (bugün):`, String(a.balance).replace(".", ","));
                      if (v != null && !Number.isNaN(parseAmount(v))) edit.mutate({ id: a.id, balance: parseAmount(v) });
                    }}>{tl(a.balance)}</button>
                  {canMerge && d.accounts.length > 1 && (
                    <button className="btn-ghost p-1 text-stone-400 hover:text-blue-600" aria-label={`${a.name}: başka hesapla birleştir`}
                      title="Başka hesapla birleştir" onClick={() => setMerging(a)}><GitMerge size={13} /></button>
                  )}
                  <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label={`${a.name} hesabını sil`} disabled={del.isPending}
                    onClick={() => void ask(`${a.name} silinsin mi?`).then((ok) => { if (ok) del.mutate(a.id); })}><Trash2 size={13} /></button>
                </li>
              ))}
              {d.unknown_banks.map((b) => (
                <li key={b} className="flex items-center gap-2">
                  <Landmark size={14} className="text-stone-400" />
                  <span className="flex-1">{b} <span className="text-xs text-amber-600">· bakiye bilinmiyor</span></span>
                  <button className="btn-ghost border border-stone-300 px-2 py-0.5 text-xs dark:border-stone-700"
                    onClick={() => {
                      const v = window.prompt(`${b} bakiyesi (bugün):`);
                      if (v != null && !Number.isNaN(parseAmount(v))) addKnown.mutate({ name: b, balance: parseAmount(v) });
                    }}>Bakiye gir</button>
                </li>
              ))}
            </ul>
            {adding ? (
              <div className="mt-2 flex gap-2">
                <select className="input w-28" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                  <option value="banka">Banka</option><option value="nakit">Nakit</option><option value="diger">Diğer</option>
                </select>
                <input className="input" placeholder={kind === "banka" ? "ör. Halkbank" : "ör. Cüzdan"} value={name} onChange={(e) => setName(e.target.value)} />
                <input className="input w-32 text-right" inputMode="decimal" placeholder="Bakiye" value={bal} onChange={(e) => setBal(e.target.value)} />
                <button className="btn-primary" disabled={!name.trim() || !(parseAmount(bal) >= 0)} onClick={() => add.mutate(undefined)}>Ekle</button>
              </div>
            ) : (
              <button className="btn-ghost mt-1 px-2 py-1 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Banka / nakit hesabı ekle</button>
            )}
            {merging && <AccountMerge from={merging} accounts={d.accounts} onClose={() => setMerging(null)} />}
          </div>

          {d.credit_cards.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-stone-500">Kredi kartları</div>
              <ul className="space-y-2 text-sm">
                {d.credit_cards.map((c) => (
                  <li key={c.title} className="flex items-start gap-2">
                    <CreditCard size={14} className="mt-0.5 shrink-0 text-stone-400" />
                    <span className="min-w-0 flex-1">
                      {c.title}
                      <span className="block text-xs text-stone-500">
                        son ekstre {dateTR(c.last_period_end)} · {tl(c.last_total)}
                        {c.unpaid > 0 ? <span className="text-red-600"> · ödenmedi, son ödeme {c.last_due ? dateTR(c.last_due) : "?"}</span>
                          : c.paid_at ? ` · ödendi ${dateTR(c.paid_at)}` : ""}
                        {c.next_estimate != null && !c.cycle && ` · sonraki ~${tl(c.next_estimate)}${c.next_due ? ` (son ödeme ${dateTR(c.next_due)})` : ""}`}
                      </span>
                      {c.cycle && <CycleLine c={c.cycle} />}
                    </span>
                    <span className={cx("shrink-0 tabular-nums", c.unpaid > 0 ? "text-red-600" : "text-stone-500")}>
                      {c.unpaid > 0 ? `−${tl(c.unpaid)}` : "borç yok"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(d.receivable > 0 || d.payable > 0 || d.zakat_due > 0) && (
            <ul className="space-y-1 text-sm">
              {d.receivable > 0 && <li className="flex justify-between"><span>Alacakların</span><span className="tabular-nums text-green-700">+{tl(d.receivable)}</span></li>}
              {d.payable > 0 && <li className="flex justify-between"><span>Borçların</span><span className="tabular-nums text-red-600">−{tl(d.payable)}</span></li>}
              {d.zakat_due > 0 && (
                <li className="flex justify-between">
                  <button className="underline decoration-dotted" onClick={() => ui.go("zekat")}>Ödenmemiş zekât (geçmiş yıllar)</button>
                  <span className="tabular-nums text-red-600">−{tl(d.zakat_due)}</span>
                </li>
              )}
            </ul>
          )}
          <Wallets />
          {tot(d).card_estimated > 0 && (
            <p className="text-xs text-stone-500">{TERIM.tahminiEkstreSonrasi.ad} <TerimBilgi id="tahminiEkstreSonrasi" />: <b>{tl(d.net_after_estimates)}</b> (kesilmemiş ekstreler ~{tl(tot(d).card_estimated)} düşülünce)</p>
          )}
          <p className="text-xs text-stone-500">
            Banka bakiyesini elle girebilirsin; hesap dökümü yüklersen (elle, e-postayla ya da Telegram'dan) kendiliğinden güncellenir.
          </p>
        </div>
      )}
    </section>
  );
}
