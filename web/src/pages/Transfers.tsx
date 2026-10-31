import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { call, send } from "../lib/apiError";
import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, ChevronDown, ChevronRight, GitMerge, HandCoins, Pencil, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import { inSharedBook, isViewer, useBook, useMeta, useOpenEntry, useSave } from "../components/hooks";
import { fmtAsset, type Debt } from "../components/Debts";
import Kpi from "../components/Kpi";
import Modal from "../components/Modal";
import MonthPicker from "../components/MonthPicker";
import Segmented from "../components/Segmented";
import { cx, dateTR, maskText, monthLabel, periodBounds, periodOf, tl } from "../format";
import { userNote } from "../components/BankText";
import { useUI } from "../ui";

interface Person {
  name: string;
  sent: number;
  received: number;
  net: number;
  count: number;
  last_date: string;
  category: string | null;
  open: Record<string, number>; // açık borç/alacak birim birim (+ alacak, − borç)
  months?: { month: string; sent: number; received: number; count: number }[];
}
interface Item {
  id: number;
  entry_date: string;
  description: string;
  amount: number;
  kind: "gelir" | "gider";
  category: string;
  counterparty: string;
  note: string | null;
  /** sunucu 90ed954: ham banka metni ve kullanıcının notu (note ikisinin birleşimi) */
  raw?: string | null;
  note_user?: string | null;
}
interface Own {
  id: number;
  transfer_date: string;
  amount: number;
  account: string | null;
  description: string | null;
  note?: string | null;
}
interface Data { people: Person[]; own: Own[]; items: Item[]; debts: Debt[]; self?: Item[] }

const shortBank = (b: string | null) => (b ? b.replace(/\s+(KATILIM\s+)?BANKASI.*$/i, "").replace(/\s+A\.?Ş\.?$/i, "") : "Hesap");

const json = <T,>(method: string, url: string, body?: unknown) => call<T>(method, url, body);
const putPerson = (name: string, body: object) => json<{ updated: number }>("PUT", `/api/transfers/people/${encodeURIComponent(name)}`, body);

const sumKind = (l: Item[], k: "gider" | "gelir") => l.filter((i) => i.kind === k).reduce((a, i) => a + i.amount, 0);

/** Havale/FAST karşı tarafları: kime ne gitti, kimden ne geldi (dönem dönem ya da tümü); kişi bazında kategori, ad düzeltme. */
export default function Transfers() {
  const ui = useUI();
  const meta = useMeta();
  const book = useBook().data;
  const viewer = isViewer(book);
  const startDay = meta.data?.cycle_start_day ?? 1;
  const [mode, setMode] = useState<"ay" | "tumu">("ay");
  const [from, to] = periodBounds(ui.month, startDay);
  const url = mode === "ay" ? `/api/transfers?start=${from}&end=${to}` : "/api/transfers";
  const q = useQuery({ queryKey: ["transfers", mode === "ay" ? ui.month : "tumu", startDay],
    queryFn: () => send<Data>(url) });
  const [open, setOpen] = useState<string | null>(null);
  const [rename, setRename] = useState<{ name: string; merge: boolean } | null>(null);
  const [own, setOwn] = useState<Own | null>(null);
  const openEntry = useOpenEntry();
  const save = useSave(({ name, body }: { name: string; body: object }) => putPerson(name, body), (r) => {
    ui.toast(r.updated ? `${r.updated} hareketin kategorisi güncellendi` : "Kategori kaydedildi");
  });
  const options = useMemo(() => [...new Set([...(meta.data?.categories ?? []), ...(meta.data?.income_categories ?? []),
    "Aile", "Borç / Alacak", "Kira & Aidat"])], [meta.data]);
  const edit = (i: Item) => void openEntry(i.id, i.entry_date, (row) => ui.openEntry(row)).then((ok) => { if (!ok) ui.toast("Kayıt bulunamadı"); });

  const data = q.data;
  // kendi hesapların arası: sunucu tüm zamanı verir, dönem seçiliyse burada süzülür
  const ownList = (data?.own ?? []).filter((o) => mode === "tumu" || (o.transfer_date >= from && o.transfer_date <= to));
  const people = (data?.people ?? []).filter((p) => mode === "tumu" ? p.count > 0 || (data?.debts ?? []).some((d) => d.person === p.name) : p.count > 0);
  const sent = people.reduce((s, p) => s + p.sent, 0);
  const received = people.reduce((s, p) => s + p.received, 0);
  const ownIn = ownList.filter((o) => o.amount > 0).reduce((s, o) => s + o.amount, 0);
  const openDebts = (data?.debts ?? []).filter((d) => !d.settled_at && !d.written_off_at && d.remaining > 0);
  const selfItems = data?.self ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">Transferler</h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            Kime ne gönderdin, kimden ne geldi; kişi kişi. Kişiye kategori verirsen tüm hareketleri o kategoriye geçer.
            Kendi hesapların arası aktarımlar gelir/gider sayılmaz, en altta ayrı durur.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented value={mode} onChange={setMode} options={[{ value: "ay", label: "Aylık" }, { value: "tumu", label: "Tümü" }]} />
          {mode === "ay" && <MonthPicker value={ui.month} onChange={ui.setMonth} />}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label="Kişilere giden" value={sent} tone="red" />
        <Kpi label="Kişilerden gelen" value={received} tone="green" />
        <div className="col-span-2 sm:col-span-1"><Kpi label="Kendi hesapların arası" value={ownIn} sub="toplama katılmaz" /></div>
      </div>

      {openDebts.length > 0 && (
        <button className="card flex w-full items-center gap-3 p-4 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => ui.go("borclar")}>
          <HandCoins size={18} className="shrink-0 text-stone-500" aria-hidden />
          <span className="min-w-0 flex-1 text-sm">
            <b>{openDebts.length} açık borç/alacak</b>
            <span className="text-stone-500 dark:text-stone-400"> · Borç / Alacak sayfasında takip et</span>
          </span>
          <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
        </button>
      )}

      {q.isLoading && <div className="text-sm text-stone-500">Yükleniyor…</div>}
      {q.error && <p className="text-sm text-red-600">{(q.error as Error).message}</p>}

      {data && (
        <section className="card divide-y divide-stone-100 dark:divide-stone-800">
          {people.length === 0 && (
            <div className="space-y-2 p-4 text-sm text-stone-500 dark:text-stone-400">
              {mode === "ay" ? <>
                <p>{monthLabel(ui.month)} döneminde kişilere giden ya da gelen para yok.</p>
                <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => setMode("tumu")}>Tüm zamanları göster</button>
              </> : <p>Henüz transfer yok. Ekstreler sayfasından banka dökümü (PDF/Excel) yükle.</p>}
            </div>
          )}
          {people.map((p) => {
            const on = open === p.name;
            const mine = data.items.filter((i) => i.counterparty === p.name);
            const debts = data.debts.filter((d) => d.person === p.name);
            return (
              <div key={p.name}>
                <button className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
                  aria-expanded={on} onClick={() => setOpen(on ? null : p.name)}>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium break-words">{maskText(p.name)}</div>
                    <div className="text-xs text-stone-500 dark:text-stone-400">
                      {p.count ? `${p.count} hareket · son ${dateTR(p.last_date)}` : "Yalnız borç / alacak kaydı"}{p.category && ` · ${p.category}`}</div>
                    {Object.entries(p.open ?? {}).map(([asset, v]) => (
                      <span key={asset} className={cx("mt-1 mr-1 inline-block rounded-full px-1.5 py-0.5 text-xs",
                        v < 0 ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                          : "bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-300")}>
                        {v < 0 ? "borcun " : "alacağın "}{fmtAsset(Math.abs(v), asset)}
                      </span>
                    ))}
                  </div>
                  <div className="shrink-0 text-right text-sm tabular-nums">
                    {p.sent > 0 && <div className="flex items-center justify-end gap-1"><ArrowUpRight size={14} className="text-red-500" aria-label="gönderdin" />{tl(p.sent)}</div>}
                    {p.received > 0 && <div className="flex items-center justify-end gap-1 text-green-700 dark:text-green-400"><ArrowDownLeft size={14} aria-label="geldi" />{tl(p.received)}</div>}
                  </div>
                  <ChevronDown size={16} aria-hidden className={cx("shrink-0 text-stone-400 transition-transform", on && "rotate-180")} />
                </button>
                {on && (
                  <PersonDetail name={p.name} moves={mine} grouped={mode === "tumu"} startDay={startDay} debts={debts}
                    category={!viewer ? (
                      <div className="sm:w-1/2">
                        <span className="label">Kategori (tüm hareketlerine uygulanır)</span>
                        <CategoryPicker value={p.category ?? ""} options={options}
                          onChange={(v) => save.mutate({ name: p.name, body: { category: v } })} />
                      </div>
                    ) : null}
                    canEdit={!viewer} onEdit={edit} onRename={(merge) => setRename({ name: p.name, merge })}
                    onDebts={() => ui.go("borclar")} />
                )}
              </div>
            );
          })}
        </section>
      )}

      {selfItems.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><UserRound size={18} aria-hidden /> Kendi adına</h2>
          <p className="mb-2 text-xs text-stone-500 dark:text-stone-400">Karşı tarafı senin adın olan hareketler kişi sayılmaz; kendi hesapların arası aktarım olabilir (Hesaplar'da eşleşir).</p>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {selfItems.map((i) => <MoveRow key={i.id} i={i} onEdit={!viewer ? edit : undefined} />)}
          </ul>
        </section>
      )}

      {ownList.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><ArrowLeftRight size={18} aria-hidden /> Kendi hesapların arası</h2>
          <p className="mb-2 text-xs text-stone-500 dark:text-stone-400">Gelir/gider toplamlarına katılmaz; sadece para nereden nereye geçmiş görmen için.
            {!inSharedBook(book) && " Dokunarak açıklamasını düzelt ya da virman değilse gelir/gider kaydına çevir."}</p>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {ownList.map((o) => {
              const body = (
                <>
                  <span className="w-20 shrink-0 text-xs text-stone-500 dark:text-stone-400">{dateTR(o.transfer_date)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block break-words">{maskText(o.description || `${shortBank(o.account)} ${o.amount > 0 ? "← giriş" : "→ çıkış"}`)}</span>
                    <span className="block text-xs text-stone-500 dark:text-stone-400">{shortBank(o.account)} · {o.amount > 0 ? "hesaba giriş" : "hesaptan çıkış"}{o.note && ` · ${o.note}`}</span>
                  </span>
                  <span className={cx("shrink-0 tabular-nums", o.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(o.amount)}</span>
                </>
              );
              return (
                <li key={o.id}>
                  {inSharedBook(book) ? <div className="flex items-start gap-2 py-2">{body}</div> : (
                    <button className="flex w-full items-start gap-2 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => setOwn(o)}>{body}</button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {rename && data && <RenamePerson name={rename.name} merge={rename.merge} people={data.people.map((p) => p.name)}
        count={data.items.filter((i) => i.counterparty === rename.name).length}
        debts={data.debts.filter((d) => d.person === rename.name).length}
        onClose={() => setRename(null)} onDone={(n) => { setRename(null); setOpen(n); }} />}
      {own && <OwnTransferForm o={own} onClose={() => setOwn(null)} />}
    </div>
  );
}

function MoveRow({ i, onEdit }: { i: Item; onEdit?: (i: Item) => void }) {
  const out = i.kind === "gider";
  const body = (
    <>
      <span className="w-20 shrink-0 text-xs text-stone-500 dark:text-stone-400">{dateTR(i.entry_date)}</span>
      <span className="min-w-0 flex-1">
        <span className="block break-words">{maskText(i.description)}</span>
        {(() => {
          const sub = [i.category, userNote(i) ? maskText(userNote(i)!) : null].filter(Boolean).join(" · ");
          return sub ? <span className="block text-xs text-stone-500 dark:text-stone-400">{sub}</span> : null;
        })()}
      </span>
      <span className={cx("shrink-0 tabular-nums", !out && "text-green-700 dark:text-green-400")}>
        <span className="sr-only">{out ? "gönderildi " : "geldi "}</span>{out ? "↑ " : "↓ "}{tl(i.amount)}
      </span>
    </>
  );
  return (
    <li>
      {onEdit ? (
        <button className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => onEdit(i)}>{body}</button>
      ) : <div className="flex items-start gap-2 px-3 py-2">{body}</div>}
    </li>
  );
}

/** Açılan kişi: hareketler (Tümü'nde döneme göre gruplu), kategori, borç/alacak, ad düzeltme / birleştirme. */
function PersonDetail({ name, moves, grouped, startDay, debts, category, canEdit, onEdit, onRename, onDebts }: {
  name: string; moves: Item[]; grouped: boolean; startDay: number; debts: Debt[]; category: React.ReactNode;
  canEdit: boolean; onEdit: (i: Item) => void; onRename: (merge: boolean) => void; onDebts: () => void;
}) {
  const [more, setMore] = useState(false);
  const groups = useMemo(() => {
    const by = new Map<string, Item[]>();
    for (const x of moves) { const k = grouped ? periodOf(x.entry_date, startDay) : ""; by.set(k, [...(by.get(k) ?? []), x]); }
    return [...by.entries()].sort(([a], [b]) => b.localeCompare(a));
  }, [moves, grouped, startDay]);
  const shown = more ? groups : groups.slice(0, 4);
  const openDebts = debts.filter((d) => !d.settled_at && !d.written_off_at && d.remaining > 0);
  return (
    <div className="space-y-3 px-4 pb-4">
      {shown.map(([ym, list]) => (
        <section key={ym || "hepsi"} aria-label={ym ? monthLabel(ym) : `${name} hareketleri`}
          className="overflow-hidden rounded-lg border border-stone-200 dark:border-stone-700">
          {grouped && (
            <div className="flex flex-wrap justify-between gap-x-3 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
              <span>{monthLabel(ym)}</span>
              <span className="tabular-nums">
                {[sumKind(list, "gider") ? `↑ gönderdin ${tl(sumKind(list, "gider"))}` : null, sumKind(list, "gelir") ? `↓ geldi ${tl(sumKind(list, "gelir"))}` : null].filter(Boolean).join(" · ")}
              </span>
            </div>
          )}
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {list.map((i) => <MoveRow key={i.id} i={i} onEdit={canEdit ? onEdit : undefined} />)}
          </ul>
        </section>
      ))}
      {groups.length > 4 && (
        <button className="text-sm text-blue-600 underline dark:text-blue-400" aria-expanded={more} onClick={() => setMore(!more)}>
          {more ? "Daha az" : `${groups.length - 4} dönem daha`}</button>
      )}
      {!moves.length && <p className="text-sm text-stone-500">Bu dönemde hareket yok.</p>}
      {debts.length > 0 && (
        <button className="flex w-full items-center gap-2 rounded-lg border border-stone-200 px-3 py-2 text-left text-sm hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/50" onClick={onDebts}>
          <HandCoins size={16} className="shrink-0 text-stone-500" aria-hidden />
          <span className="min-w-0 flex-1"><b>Borç / Alacak</b>
            <span className="block text-xs text-stone-500 dark:text-stone-400">
              {openDebts.length ? openDebts.map((d) => `${d.direction === "alacak" ? "alacağın" : "borcun"} ${fmtAsset(d.remaining, d.asset)}`).join(" · ") : `${debts.length} kayıt, hepsi kapandı`}</span>
          </span>
          <ChevronRight size={16} className="shrink-0 text-stone-400" aria-hidden />
        </button>
      )}
      {category}
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => onRename(false)}><Pencil size={14} aria-hidden /> Adı düzenle</button>
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => onRename(true)}><GitMerge size={14} aria-hidden /> Başka kişiyle birleştir</button>
        </div>
      )}
    </div>
  );
}

/** Kişi adını düzelt ya da başka kişiyle birleştir: PATCH /api/transfers/people/{ad} {name} — hareketler, açıklamalar,
 *  borç defteri ve kişi ayarı yeni ada geçer; aynı ad varsa iki kişi birleşir. */
function RenamePerson({ name, merge, people, count, debts, onClose, onDone }: {
  name: string; merge: boolean; people: string[]; count: number; debts: number; onClose: () => void; onDone: (to: string) => void;
}) {
  const ui = useUI();
  const [to, setTo] = useState(merge ? "" : name);
  const others = people.filter((p) => p !== name);
  const t = to.trim();
  const lower = t.toLocaleLowerCase("tr");
  const joins = others.find((p) => p.toLocaleLowerCase("tr") === lower);
  const hits = (lower ? others.filter((p) => p.toLocaleLowerCase("tr").includes(lower) && p !== joins) : others).slice(0, 8);
  const ok = !!t && t !== name && t.length <= 80;
  const save = useSave(() => json<{ entries: number; debts: number; merged: boolean; name: string }>("PATCH",
    `/api/transfers/people/${encodeURIComponent(name)}`, { name: joins ?? t }), (r) => {
    ui.toast(r.merged ? `${name}, ${r.name} ile birleşti` : `Ad “${r.name}” oldu`);
    onDone(r.name);
  }, { inline: true });
  return (
    <Modal title={merge ? "Başka kişiyle birleştir" : "Adı düzenle"} onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={!ok || save.isPending} onClick={() => save.mutate(undefined)}>
        {save.isPending ? "Kaydediliyor…" : joins ? "Birleştir" : "Kaydet"}</button>
    </>}>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-300">
          “{name}” adına kayıtlı {count} hareket{debts ? ` ve ${debts} borç/alacak kaydı` : ""} {merge ? "seçtiğin kişiye taşınır" : "yeni adla görünür"}.
        </p>
        <div>
          <label className="label" htmlFor="rn-name">{merge ? "Birleştirilecek kişi" : "Yeni ad"}</label>
          <input id="rn-name" className="input" value={to} maxLength={80} autoComplete="off" autoFocus
            placeholder={merge ? "Kişi seç ya da yaz" : "Ad soyad"} onChange={(e) => setTo(e.target.value)} />
        </div>
        {hits.length > 0 && (
          <div className="flex flex-wrap gap-2" role="group" aria-label="Kişiler">
            {hits.map((n) => (
              <button key={n} type="button" className="rounded-full border border-stone-300 px-2.5 py-1 text-left text-xs hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800" onClick={() => setTo(n)}>{n}</button>
            ))}
          </div>
        )}
        {joins && (
          <p className="rounded-lg bg-blue-50 p-2 text-xs text-blue-800 dark:bg-blue-950/40 dark:text-blue-300">
            “{joins}” zaten var: iki kişi tek kişi olur, hareketleri birlikte görünür.</p>
        )}
        {save.error && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{(save.error as Error).message}</p>}
      </div>
    </Modal>
  );
}

/** Kendi hesapları arası aktarım: açıklama/not (PATCH /api/own-transfers/{id}); yanlış eşleşmeyse gelir/gider kaydına çevir. */
function OwnTransferForm({ o, onClose }: { o: Own; onClose: () => void }) {
  const ui = useUI();
  const [desc, setDesc] = useState(o.description ?? "");
  const [note, setNote] = useState(o.note ?? "");
  const save = useSave(() => json("PATCH", `/api/own-transfers/${o.id}`, { description: desc.trim(), note: note.trim() }), () => {
    ui.toast("Aktarım kaydedildi"); onClose();
  }, { inline: true });
  const convert = useSave(() => json<{ kind: string }>("PATCH", `/api/own-transfers/${o.id}`, { not_transfer: true }), (r) => {
    ui.toast(r.kind === "gelir" ? "Gelir kaydına çevrildi" : "Gider kaydına çevrildi"); onClose();
  }, { inline: true });
  const err = (save.error || convert.error) as Error | null;
  const kindWord = o.amount > 0 ? "Gelir" : "Gider";
  return (
    <Modal title="Kendi hesapların arası" onClose={onClose} footer={<>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary" disabled={save.isPending || !desc.trim()} onClick={() => save.mutate(undefined)}>Kaydet</button>
    </>}>
      <div className="space-y-3">
        <div className="rounded-lg bg-stone-100 p-3 text-sm dark:bg-stone-800">
          <div className="flex justify-between gap-2"><b>{shortBank(o.account)} · {o.amount > 0 ? "hesaba giriş" : "hesaptan çıkış"}</b>
            <b className={cx("shrink-0 tabular-nums", o.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(o.amount)}</b></div>
          <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">{dateTR(o.transfer_date)} · tutar ve tarih dökümden gelir, değiştirilemez</div>
        </div>
        <div>
          <label className="label" htmlFor="ot-desc">Açıklama</label>
          <input id="ot-desc" className="input" maxLength={200} value={desc} onChange={(e) => setDesc(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="ot-note">Not</label>
          <input id="ot-note" className="input" maxLength={500} value={note} placeholder="İsteğe bağlı" onChange={(e) => setNote(e.target.value)} />
        </div>
        {/* "virman değil": soru değil, açık eylem — ne olacağı düğmede ve onayda yazar */}
        <div className="space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
          <p className="text-sm font-medium">Yanlış eşleştiyse</p>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            Bu para kendi hesapların arasında değil de başkasına gittiyse ya da başkasından geldiyse aşağıdaki düğmeyle
            {" "}{kindWord.toLocaleLowerCase("tr")} olarak say; bu ayın {kindWord.toLocaleLowerCase("tr")}lerine eklenir.
          </p>
          <button className="btn border border-stone-300 text-stone-800 hover:bg-stone-50 dark:border-stone-600 dark:text-stone-100 dark:hover:bg-stone-800"
            disabled={convert.isPending}
            onClick={() => void ask(
              `${tl(Math.abs(o.amount))} (${dateTR(o.transfer_date)}, ${shortBank(o.account)}) kendi hesapların arası aktarım sayılmayacak; ` +
              `${kindWord.toLocaleLowerCase("tr")} kaydı olur ve bu ayın toplamlarına girer. Kişi ve kategori dökümdeki açıklamadan gelir; ` +
              "sonra İşlemler'den düzeltebilirsin.",
              { title: `${kindWord} olarak sayılsın mı?`, confirm: `${kindWord} olarak say`, danger: false, irreversible: false },
            ).then((ok) => { if (ok) convert.mutate(undefined); })}>
            Kendi hesaplarım arası değil → {kindWord.toLocaleLowerCase("tr")} olarak say</button>
        </div>
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err.message}</p>}
      </div>
    </Modal>
  );
}
