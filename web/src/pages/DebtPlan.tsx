import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarCheck, Check, ChevronDown, ChevronRight, CreditCard, HandCoins, Info, LayoutDashboard, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, send } from "../lib/apiError";
import { cx, maskText, parseAmount, tl } from "../format";
import { useBook, useCap, useUiPrefs } from "../components/hooks";
import SorguDurumu, { KartIskelet, SatirIskelet } from "../components/SorguDurumu";
import { kayitDuzeni, ozetDuzeni } from "../lib/ozetDuzen";
import { useUI } from "../ui";
import { DEFAULT_LAYOUT, WIDGETS } from "./Dashboard";
import { aylikTutarHatasi, debtPlanUrl, KAYDET_MS, ONIZLE_MS, type DebtOrder, type DebtPlanData } from "../lib/debtPlan";

const H2 = ({ children }: { children: React.ReactNode }) => <h2 className="px-1 text-xs font-semibold tracking-wide text-stone-500 uppercase">{children}</h2>;
const trimTl = (x: number) => tl(x).replace(/,00(?=\D*$)/, "");

/** Borç kapatma planı (Diğer → Borç planı; Borç / Alacak başlığından da açılır). */
export default function DebtPlan() {
  const ui = useUI();
  const qc = useQueryClient();
  const on = useCap("debt_plan");
  const book = useBook().data;
  // önizleme: kullanıcı tutarı / sıralamayı değiştirdi (sorgu bu değerlerle; sunucu kaydetmez, kayıt ayrıca PUT)
  const [draft, setDraft] = useState<{ monthly?: number | null; order?: DebtOrder }>({});
  const q = useQuery({ queryKey: ["debt-plan", draft.monthly ?? null, draft.order ?? null],
    queryFn: () => send<DebtPlanData>(debtPlanUrl(draft.monthly, draft.order)), enabled: on, placeholderData: (prev) => prev });
  const r = q.data;
  // null: kullanıcı yazmadı, kutuda planın tutarı
  const [typed, setText] = useState<string | null>(null);
  const text = typed ?? (r?.monthly != null ? r.monthly.toLocaleString("tr-TR", { maximumFractionDigits: 2 }) : "");
  const [allMonths, setAllMonths] = useState(false);
  const [openMonth, setOpenMonth] = useState<string | null>(null);
  // yazılan tutar: geçerliyse sayı, değilse hata (plan son geçerli tutarla kalır)
  const parsed = typed == null ? null : typed.trim() ? parseAmount(typed) : null;
  const err = typed == null ? null : aylikTutarHatasi(parsed);
  const valid = typed != null && !err && parsed != null ? Math.round(parsed * 100) / 100 : null;
  const canEdit = !!r?.can_edit;
  const savedMonthly = r?.settings.monthly ?? null;
  const [kayit, setKayit] = useState<"bekliyor" | "kaydedildi" | null>(null);
  const sent = useRef<number | null | undefined>(undefined); // son gönderilen aylık tutar (aynısı iki kez gitmesin)
  const save = useMutation({
    mutationFn: (body: { monthly?: number | null; order?: DebtOrder }) => send("/api/debt-plan", { method: "PUT", json: body }),
    onMutate: () => setKayit("bekliyor"),
    // refetchType "all": açık olmayan parametresiz plan (Özet kartı, ilk açılış) da tazelensin; kutu ile plan ayrışmasın
    onSuccess: () => { setKayit("kaydedildi"); void qc.invalidateQueries({ queryKey: ["debt-plan"], refetchType: "all" }); },
    onError: (e: Error) => { setKayit(null); sent.current = undefined; ui.toast(`${e.message || "Kaydedilemedi"} · plan yalnız bu ekranda değişti`); },
  });
  const persist = (body: { monthly?: number | null; order?: DebtOrder }) => {
    if (!canEdit) return;
    if ("monthly" in body) {
      if (body.monthly === sent.current || (sent.current === undefined && body.monthly === savedMonthly)) return;
      sent.current = body.monthly;
    }
    save.mutate(body);
  };
  const apply = (body: { monthly?: number | null; order?: DebtOrder }) => {
    setDraft((d) => ({ ...d, ...body }));
    persist(body);
  };
  const setTo = (v: number) => { setText(v.toLocaleString("tr-TR", { maximumFractionDigits: 2 })); apply({ monthly: v }); };
  // yazarken canlı: 300 ms sonra önizleme (kaydetmeden), 1 sn durunca kayıt. Ayrı "Uygula" adımı yok.
  useEffect(() => {
    if (valid == null) return;
    const t = setTimeout(() => setDraft((d) => (d.monthly === valid ? d : { ...d, monthly: valid })), ONIZLE_MS);
    return () => clearTimeout(t);
  }, [valid]);
  const persistRef = useRef(persist);
  useEffect(() => { persistRef.current = persist; });
  useEffect(() => {
    if (valid == null || !canEdit || valid === savedMonthly) return;
    const t = setTimeout(() => persistRef.current({ monthly: valid }), KAYDET_MS);
    return () => clearTimeout(t);
  }, [valid, canEdit, savedMonthly]);
  // ekrandan çıkarken bekleyen kayıt kaybolmasın
  const pending = useRef<number | null>(null);
  useEffect(() => { pending.current = valid != null && canEdit && valid !== savedMonthly && valid !== sent.current ? valid : null; });
  useEffect(() => () => {
    if (pending.current != null) void send("/api/debt-plan", { method: "PUT", json: { monthly: pending.current } })
      .then(() => qc.invalidateQueries({ queryKey: ["debt-plan"], refetchType: "all" })).catch(() => undefined);
  }, [qc]);
  useEffect(() => {
    if (kayit !== "kaydedildi") return;
    const t = setTimeout(() => setKayit(null), 2500);
    return () => clearTimeout(t);
  }, [kayit]);
  const commitAmount = () => { if (valid != null) { setDraft((d) => ({ ...d, monthly: valid })); persist({ monthly: valid }); } };
  const old = !on || (q.error instanceof ApiError && q.error.status === 404);

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Borç planı</h1>
        <p className="text-sm text-stone-500 dark:text-stone-400">{r?.intro ?? "Borçların tek listede; önce kart borçları kapanır, faiz işlemez."}</p>
      </div>
      {old && <p role="note" className="flex gap-2 rounded-lg bg-blue-50 p-3 text-sm text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
        <Info size={16} className="mt-0.5 shrink-0" aria-hidden />Sunucun eski bir sürüm; bazı özellikler kapalı. Sunucunu güncelleyince açılır.</p>}
      {!old && !r && <SorguDurumu q={q} etiket="Borç planı yükleniyor"><div className="space-y-3"><KartIskelet satir={2} /><SatirIskelet n={4} /></div></SorguDurumu>}
      {!old && r && (r.empty ? (
        <div className="card flex flex-col items-center gap-2 p-6 text-center" data-testid="borc-plani-bos">
          <ShieldCheck size={26} className="text-green-600" aria-hidden />
          <p className="font-semibold">{r.empty.title}</p>
          <p className="text-sm text-stone-500">{r.empty.text}</p>
          <button className="btn-ghost mt-1" onClick={() => ui.go("borclar")}>Borç / Alacak <ChevronRight size={16} /></button>
        </div>
      ) : planBody(r))}
    </div>
  );

  function planBody(p: DebtPlanData) {
    const months = allMonths ? p.months : p.months.slice(0, 12);
    return (
      <>
        <section className="card space-y-3 p-4" data-testid="borc-plani-ozet">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-stone-500">Toplam borcun</p>
              <p className="text-3xl font-bold tabular-nums">{tl(p.totals.open)}</p>
            </div>
            {p.debt_free && (
              <div className="min-w-0 text-right">
                <p className="text-sm text-stone-500">Borçsuz olacağın ay</p>
                <p className="text-xl font-semibold text-green-700 dark:text-green-400">{p.debt_free.label}</p>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <span className="rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-800/60">
              <span className="block text-xs text-stone-500">Kart borcu ({p.totals.card_count})</span>
              <span className="block text-sm font-semibold tabular-nums">{tl(p.totals.cards)}</span></span>
            <span className="rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-800/60">
              <span className="block text-xs text-stone-500">Elden borç ({p.totals.person_count})</span>
              <span className="block text-sm font-semibold tabular-nums">{p.hidden.people ? "—" : tl(p.totals.people)}</span></span>
          </div>
          {p.debt_free && <p className="text-sm text-stone-600 dark:text-stone-300" data-testid="borcsuz">{p.debt_free.text}</p>}
          {p.hidden.people && <p className="text-xs text-stone-500">Borç / Alacak bu defterde sana kapalı; elden borçlar planda yok.</p>}
        </section>

        <section className="card space-y-3 p-4" aria-label="Plan ayarı">
          <label className="block space-y-1">
            <span className="flex items-center justify-between gap-2 text-sm font-medium">Her ay borçlara ayırabileceğin
              {/* kutudaki tutar her zaman planın tutarı; kayıt yoksa öneridir */}
              {typed == null && p.monthly_source === "suggestion" && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-950/50 dark:text-blue-200" data-testid="oneri-etiket">Öneri</span>}</span>
            <input className={cx("input w-full text-right tabular-nums", err && "border-red-400 dark:border-red-700")} inputMode="decimal" placeholder="ör. 15.000" value={text}
              aria-label="Aylık tutar" aria-invalid={!!err} aria-describedby="aylik-durum" onChange={(e) => setText(e.target.value)} onBlur={commitAmount}
              onKeyDown={(e) => { if (e.key === "Enter") commitAmount(); }} />
          </label>
          <p id="aylik-durum" aria-live="polite" className="min-h-4 text-xs" data-testid="aylik-durum">
            {err ? <span className="text-red-700 dark:text-red-400">{err}{p.monthly != null && <> Plan şimdilik {trimTl(p.monthly)} ile.</>}</span>
              : kayit === "bekliyor" ? <span className="text-stone-500">Kaydediliyor…</span>
              : kayit === "kaydedildi" ? <span className="inline-flex items-center gap-1 text-green-700 dark:text-green-400"><Check size={13} aria-hidden />Kaydedildi</span>
              : q.isFetching && typed != null ? <span className="text-stone-500">Hesaplanıyor…</span> : null}
          </p>
          {p.suggestion && (p.monthly_source === "suggestion" && typed == null ? (
            <p className="text-xs text-stone-500">{maskText(p.suggestion.text)}</p>
          ) : (
            <div className="space-y-0.5 text-xs text-stone-500" data-testid="oneri">
              {p.suggestion.amount > 0 && p.monthly !== p.suggestion.amount && (
                <p className="flex flex-wrap items-center gap-x-2"><span className="tabular-nums">Öneri: {maskText(trimTl(p.suggestion.amount))}</span>
                  <span aria-hidden>·</span>
                  <button className="font-medium text-blue-700 underline dark:text-blue-400" onClick={() => setTo(p.suggestion!.amount)}>Öneriyi kullan</button></p>)}
              <p>{maskText(p.suggestion.text)}</p>
            </div>
          ))}
          <div role="radiogroup" aria-label="Sıralama" className="grid gap-2 sm:grid-cols-3">
            {p.orders.map((o) => (
              <button key={o.key} role="radio" aria-checked={p.order === o.key} onClick={() => apply({ order: o.key })}
                className={cx("rounded-xl border p-3 text-left transition-colors",
                  p.order === o.key ? "border-blue-500 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/40" : "border-stone-200 dark:border-stone-700")}>
                <span className="flex items-center gap-1.5 text-sm font-semibold">
                  {p.order === o.key && <Check size={15} className="text-blue-600 dark:text-blue-400" aria-hidden />}{o.label}</span>
                <span className="mt-0.5 block text-xs text-stone-600 dark:text-stone-400">{o.text}</span>
              </button>
            ))}
          </div>
          {!p.can_edit && <p className="text-xs text-stone-500">Bu defterde sadece görüntüleme yetkin var: değişiklikler kaydedilmez, yalnız burada denersin.</p>}
        </section>

        {p.shortfall && (
          <section role="alert" className="space-y-2 rounded-2xl border border-red-200 bg-red-50 p-4 text-red-900 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-100" data-testid="borc-plani-yetmiyor">
            <p className="flex items-center gap-2 font-semibold"><AlertTriangle size={18} aria-hidden />{p.shortfall.title}</p>
            <p className="text-sm">{maskText(p.shortfall.text)}</p>
            <p className="text-xs opacity-90">{p.shortfall.hint}</p>
            <button className="btn-primary" onClick={() => setTo(p.shortfall!.min_monthly)}>
              Aylık tutarı {trimTl(p.shortfall.min_monthly)} yap</button>
          </section>
        )}
        {p.pace && !p.shortfall && (
          <p role="note" className="rounded-xl bg-stone-50 p-3 text-sm text-stone-700 dark:bg-stone-800/60 dark:text-stone-200" data-testid="borc-plani-hiz">{p.pace.text}</p>
        )}
        {p.stuck && (
          <p role="note" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
            <b>{p.stuck.title}.</b> {p.stuck.text}</p>
        )}
        {p.monthly == null && (
          <p role="note" className="rounded-xl bg-blue-50 p-3 text-sm text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
            Her ay borçlara ne kadar ayırabileceğini yaz; plan ay ay çıksın.</p>
        )}

        {p.this_month.items.length > 0 && (
          <section className="space-y-1.5" aria-label="Bu ay ödenecekler">
            <H2>Bu ay ödenecekler · {p.this_month.label}</H2>
            <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
              {p.this_month.items.map((i) => (
                <li key={`${i.key}-${i.done ? "d" : "p"}`} className="flex items-center gap-3 p-3" data-testid="bu-ay">
                  <span className={cx("flex size-7 shrink-0 items-center justify-center rounded-full border-2",
                    i.done ? "border-green-600 bg-green-600 text-white" : "border-stone-300 dark:border-stone-600")} aria-hidden>
                    {i.done && <Check size={15} strokeWidth={3} />}</span>
                  <span className="min-w-0 flex-1">
                    <span className={cx("block font-medium break-words", i.done && "text-stone-500 line-through")}>{i.title}</span>
                    <span className="block text-xs text-stone-500">{maskText(i.text)}</span></span>
                  {/* dar ekranda tutar ve düğme alt alta: ad ve açıklama sıkışmasın */}
                  <span className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
                    <span className="font-semibold tabular-nums">{tl(i.amount)}</span>
                    {!i.done && p.can_edit && <button className="btn-ghost px-2 py-1 text-sm" onClick={() => ui.openPay(i.payable)}
                      aria-label={`${i.title}: ödedim`}>Ödedim</button>}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="space-y-1.5" aria-label="Borçların">
          <H2>Borçların · kapanış sırasıyla</H2>
          <ul className="space-y-2">
            {p.debts.map((d) => (
              <li key={d.key} className="card space-y-2 p-3" data-testid="borc">
                <div className="flex items-start gap-3">
                  <span className={cx("flex size-8 shrink-0 items-center justify-center rounded-lg",
                    d.kind === "card" ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400" : "bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-400")} aria-hidden>
                    {d.kind === "card" ? <CreditCard size={17} /> : <HandCoins size={17} />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium break-words">{d.order}. {d.title}</span>
                    <span className={cx("block text-xs", d.overdue && d.kind === "card" ? "text-red-600 dark:text-red-400" : "text-stone-500")}>{d.subtitle}</span></span>
                  <span className="shrink-0 text-right">
                    <span className="block font-semibold tabular-nums">{tl(d.remaining)}</span>
                    <span className="block text-xs text-stone-500">{d.closes ? `Kapanış: ${d.closes.label}` : p.monthly ? "Kapanmıyor" : ""}</span></span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800" role="progressbar" aria-label={`${d.title} ödenen`}
                  aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(d.progress * 100)}>
                  <div className={cx("h-full rounded-full", d.kind === "card" ? "bg-blue-600" : "bg-green-600")} style={{ width: `${Math.max(d.progress * 100, d.progress > 0 ? 3 : 0)}%` }} />
                </div>
                {d.paid > 0 && <p className="text-xs text-stone-500 tabular-nums">{tl(d.paid)} ödendi · {tl(d.total)} borcun %{Math.round(d.progress * 100)}</p>}
                {d.warning && <p className={cx("flex gap-2 rounded-lg p-2 text-xs", d.overdue ? "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-100" : "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100")}>
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden /><span>{maskText(d.warning)}{d.rate_note && <span className="block opacity-80">{d.rate_note}</span>}</span></p>}
                {d.promise && <p className={cx("text-xs", d.promise.missed ? "text-amber-800 dark:text-amber-300" : "text-stone-500")}>{d.promise.text}</p>}
                {d.future_installments && <p className="text-xs text-stone-500">{maskText(d.future_installments.text)}</p>}
              </li>
            ))}
          </ul>
        </section>

        {p.months.length > 0 && (
          <section className="space-y-1.5" aria-label="Ay ay plan">
            <H2>Ay ay plan</H2>
            <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
              {months.map((m) => {
                const open = openMonth === m.month;
                const closing = m.payments.filter((x) => x.closes);
                return (
                  <li key={m.month}>
                    <button className="flex w-full items-center gap-3 p-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" aria-expanded={open}
                      onClick={() => setOpenMonth(open ? null : m.month)}>
                      <CalendarCheck size={17} className={cx("shrink-0", m.left <= 0 ? "text-green-600" : "text-stone-400")} aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{m.label}</span>
                        <span className="block text-xs text-stone-500">
                          {closing.length ? `Kapanan: ${closing.map((x) => x.title).join(", ")}` : `${m.payments.length} ödeme`}
                          {m.left > 0 ? ` · kalan ${tl(m.left)}` : " · borç kalmadı"}
                          {m.interest > 0 && !p.hidden.fees ? ` · faiz ~${tl(m.interest)}` : ""}</span></span>
                      <span className="shrink-0 font-semibold tabular-nums">{tl(m.total)}</span>
                      <ChevronDown size={16} className={cx("shrink-0 text-stone-400 transition-transform", open && "rotate-180")} aria-hidden />
                    </button>
                    {open && (
                      <ul className="space-y-1 px-3 pb-3 pl-11 text-sm">
                        {m.payments.map((x) => (
                          <li key={x.key} className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 break-words">{x.title}{x.closes && <span className="ml-1 text-xs text-green-700 dark:text-green-400">· kapanır</span>}</span>
                            <span className="tabular-nums">{tl(x.amount)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
            {p.months.length > 12 && <button className="btn-ghost w-full" onClick={() => setAllMonths(!allMonths)}>
              {allMonths ? "İlk 12 ayı göster" : `Tümünü göster (${p.months.length} ay)`}</button>}
          </section>
        )}

        {p.excluded.length > 0 && (
          <section className="space-y-1.5">
            <H2>Planda olmayanlar</H2>
            <ul className="card divide-y divide-stone-100 text-sm dark:divide-stone-800">
              {p.excluded.map((x) => <li key={x.key} className="p-3"><b>{x.title}</b> <span className="text-stone-500">{maskText(x.text)}</span></li>)}
            </ul>
          </section>
        )}

        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => ui.go("borclar")}><HandCoins size={16} /> Borç / Alacak</button>
          {p.summary_card.eligible && <OzetToggle />}
        </div>
        {book?.hide_fees && <p className="text-xs text-stone-500">Bu defterde faiz tutarları gizli; uyarılar tutarsız gösterilir.</p>}
      </>
    );
  }
}

/** Özet'te küçük kart (yalnız ≥2 açık borçta, kullanıcı eklerse; Özeti düzenle'den de kaldırılır). */
function OzetToggle() {
  const ui = useUI();
  const { ui: prefs, save } = useUiPrefs();
  const known = (id: string) => !!WIDGETS[id];
  const layout = ozetDuzeni(prefs.layout, known, DEFAULT_LAYOUT);
  const inOzet = layout.includes("borc-plani");
  return (
    <button className="btn-ghost" aria-pressed={inOzet} onClick={() => {
      const next = inOzet ? layout.filter((x) => x !== "borc-plani") : [...layout, "borc-plani"];
      save.mutate({ layout: kayitDuzeni(next) }, { onSuccess: () => ui.toast(inOzet ? "Özet'ten kaldırıldı" : "Özet'e eklendi") });
    }}><LayoutDashboard size={16} /> {inOzet ? "Özet'ten kaldır" : "Özet'te göster"}</button>
  );
}
