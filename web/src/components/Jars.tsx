import { useQuery } from "@tanstack/react-query";
import { Camera, ChevronDown, Minus, Coins, Pencil, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { cx, dateTR, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import AccountSelect from "./AccountSelect";
import { useSave } from "./hooks";
import Modal from "./Modal";

interface Move { id: number; move_date: string; amount: number; note: string | null; auto: number; cost: number | null }
interface Jar {
  unit: string; unit_label?: string; unit_symbol?: string; value?: number | null; unit_price?: number | null; cost?: number;
  gain?: number | null; buy_due?: boolean; bought_this_month?: number;
  id: number; name: string; goal: number | null; deadline: string | null; account_id: number | null; account_name: string | null;
  monthly: number | null; auto_day: number | null; note: string | null; closed_at: string | null;
  balance: number; moves: Move[]; pct?: number; left?: number; need_per_month?: number;
  pace: number; eta?: string; on_track?: boolean;
}

// TL dışı kumbara: her ay altın/döviz alımı (varlık olarak eklenir)
const UNITS: [string, string][] = [["TRY", "TL"], ["ALTIN_24", "Gram altın"], ["CEYREK", "Çeyrek"], ["YARIM", "Yarım"],
  ["TAM", "Tam altın"], ["CUMHURIYET", "Cumhuriyet"], ["USD", "Dolar"], ["EUR", "Euro"], ["GUMUS", "Gümüş (gr)"]];
const SYM: Record<string, string> = { ALTIN_24: "gr", GUMUS: "gr", USD: "$", EUR: "€", GBP: "£" };
const sym = (unit: string) => SYM[unit] ?? "adet";
export const qty = (n: number, s: string) => (s === "gr" || s === "adet" ? `${Number(n.toFixed(3))} ${s}` : `${s}${Number(n.toFixed(2))}`);
const isUnit = (j?: { unit?: string }) => !!j?.unit && j.unit !== "TRY";

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

function JarForm({ jar, onClose }: { jar?: Jar; onClose: () => void }) {
  const ui = useUI();
  const [unit, setUnit] = useState(jar?.unit ?? "TRY");
  const u = unit !== "TRY";
  const [name, setName] = useState(jar?.name ?? "");
  const [goal, setGoal] = useState(jar?.goal ? String(jar.goal) : "");
  const [deadline, setDeadline] = useState(jar?.deadline ?? "");
  const [account, setAccount] = useState<number | null>(jar?.account_id ?? null);
  const [initial, setInitial] = useState("");
  const [auto, setAuto] = useState(!!jar?.monthly);
  const [monthly, setMonthly] = useState(jar?.monthly ? String(jar.monthly) : "");
  const [day, setDay] = useState(String(jar?.auto_day ?? 1));
  const body = {
    ...(jar ? {} : { unit }),
    name: name.trim(), goal: parseAmount(goal) > 0 ? parseAmount(goal) : null, deadline: deadline || null, account_id: account,
    monthly: auto && parseAmount(monthly) > 0 ? parseAmount(monthly) : null, auto_day: auto && parseAmount(monthly) > 0 ? Number(day) : null,
  };
  const save = useSave(() => jar ? json("PATCH", `/api/jars/${jar.id}`, body)
    : json("POST", "/api/jars", { ...body, initial: parseAmount(initial) || 0 }), () => { ui.toast("Kaydedildi"); onClose(); });
  return (
    <Modal title={jar ? "Kumbarayı düzenle" : "Yeni kumbara"} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!name.trim() || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}>
      <div className="space-y-3">
        {!jar && (
          <div>
            <label className="label" htmlFor="j-unit">Ne biriktiriyorsun?</label>
            <select id="j-unit" className="input" value={unit} onChange={(e) => setUnit(e.target.value)}>
              {UNITS.map(([k, l]) => <option key={k} value={k}>{k === "TRY" ? "TL (para ayır)" : `${l} (her ay al)`}</option>)}
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="j-name">Ne için?</label>
          <input id="j-name" className="input" placeholder="ör. Tatil, Telefon, Acil durum" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="j-goal">{u ? `Hedef (${sym(unit)}, ops.)` : "Hedef tutar (ops.)"}</label>
            <input id="j-goal" className="input text-right" inputMode="decimal" value={goal} onChange={(e) => setGoal(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="j-dl">Son tarih (ops.)</label>
            <input id="j-dl" type="date" className="input" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
          </div>
        </div>
        {!u && <AccountSelect id="j-acc" label="Para nerede duruyor? (ops.)" value={account} onChange={setAccount} />}
        {!jar && (
          <div>
            <label className="label" htmlFor="j-init">{u ? `Bu hedef için zaten aldığın (${sym(unit)}, ops.)` : "Şimdi ayırdığın (ops.)"}</label>
            <input id="j-init" className="input text-right" inputMode="decimal" value={initial} onChange={(e) => setInitial(e.target.value)} />
          </div>
        )}
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> {u ? "Her ay al (Özet'te hatırlatılır)" : "Her ay otomatik ayır"}
        </label>
        {auto && (
          <div className="grid grid-cols-2 gap-2">
            <input className="input text-right" inputMode="decimal" placeholder={u ? `Aylık (${sym(unit)})` : "Aylık tutar"} value={monthly} onChange={(e) => setMonthly(e.target.value)} />
            <select className="input" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Ayın günü">
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>Her ayın {d}. günü</option>)}
            </select>
          </div>
        )}
        <p className="text-xs text-stone-500">
          {u ? <>Aldığında "Aldım" de: Altın ve döviz varlıklarına senin adına alış tarihi ve bedeliyle eklenir (zekâtta ve net
            durumda sayılır), ödeme gider olarak yazılır ve döküm gelince eşleşir.</>
            : <>Para hesabında ya da cebinde durmaya devam eder; sadece "harcanabilir" bakiyenden düşer. Hedefe ulaşınca kapat,
            ayrılan tutar serbest kalır (harcamanın kendisi zaten ekstreden/dökümden gelir).</>}
        </p>
      </div>
    </Modal>
  );
}

function MoveForm({ jar, dir, onClose }: { jar: Jar; dir: "yatir" | "cek"; onClose: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState(dir === "yatir" && jar.need_per_month ? String(jar.need_per_month) : "");
  const [note, setNote] = useState("");
  const save = useSave(() => json("POST", `/api/jars/${jar.id}/moves`, { amount: parseAmount(amount), direction: dir, note }),
    () => { ui.toast(dir === "yatir" ? "Kumbaraya eklendi" : "Kumbaradan çekildi"); onClose(); });
  return (
    <Modal title={`${jar.name} · ${dir === "yatir" ? "para ayır" : "para çek"}`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(amount) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>{dir === "yatir" ? "Ayır" : "Çek"}</button>}>
      <div className="space-y-3">
        <input className="input text-right text-lg" inputMode="decimal" autoFocus placeholder="Tutar" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input className="input" placeholder="Not (ops.)" value={note} onChange={(e) => setNote(e.target.value)} />
        <p className="text-xs text-stone-500">Kumbarada şu an {tl(jar.balance)} var.</p>
      </div>
    </Modal>
  );
}

/** Altın/döviz kumbarası: "aldım" → varlık + gider kaydı + kumbara hareketi. */
function BuyForm({ jar, onClose }: { jar: Jar; onClose: () => void }) {
  const ui = useUI();
  const s = jar.unit_symbol ?? sym(jar.unit);
  const [amount, setAmount] = useState(String(jar.monthly ?? 1));
  const est = (jar.unit_price ?? 0) * (parseAmount(amount) || 0);
  const [cost, setCost] = useState("");
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [payment, setPayment] = useState<"havale" | "nakit" | "kredi_karti">("havale");
  const [account, setAccount] = useState<number | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState(false);
  // kuyumcu fişi: gerçek ödenen tutar ve tarih (yerel OCR ya da yapay zekâ)
  const readReceipt = async (f: File | undefined) => {
    if (!f) return;
    setReading(true);
    try {
      const fd = new FormData();
      fd.append("file", f, f.name);
      const r = await fetch("/api/documents/parse", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail ?? "Fiş okunamadı");
      if (d.total) setCost(String(d.total));
      if (d.date) setDay(d.date);
      ui.toast(d.total ? `Fişten ${tl(d.total)} okundu; kontrol et` : "Tutar okunamadı; elle yaz");
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      setReading(false);
    }
  };
  const save = useSave(() => json("POST", `/api/jars/${jar.id}/buy`, {
    quantity: parseAmount(amount), cost: parseAmount(cost) || Math.round(est * 100) / 100, buy_date: day, payment,
    account_id: payment === "kredi_karti" ? null : account,
  }), () => { ui.toast(`${qty(parseAmount(amount), s)} eklendi`); onClose(); });
  return (
    <Modal title={`${jar.name} · aldım`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(amount) > 0) || !(parseAmount(cost) > 0 || est > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs">Ne kadar ({s})
            <input className="input mt-1 text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label className="text-xs">Ödediğin (₺)
            <input className="input mt-1 text-right" inputMode="decimal" placeholder={est ? tl(est) : ""} value={cost} onChange={(e) => setCost(e.target.value)} />
          </label>
        </div>
        {est > 0 && !cost && <p className="text-xs text-stone-500">Boş bırakırsan bugünkü fiyatla {tl(est)} yazılır.
          Banka/kart ekstresi gelince gerçek ödenen tutara kendiliğinden düzelir.</p>}
        <button type="button" className="btn-ghost border border-stone-300 px-2 py-1 text-sm dark:border-stone-700" disabled={reading}
          onClick={() => file.current?.click()}><Camera size={14} /> {reading ? "Okunuyor…" : "Fişi okut (tutar ve tarih dolsun)"}</button>
        <input ref={file} type="file" accept="image/*,application/pdf" capture="environment" className="hidden"
          onChange={(e) => { readReceipt(e.target.files?.[0]); e.target.value = ""; }} />
        <input type="date" className="input" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Alış tarihi" />
        <select className="input" value={payment} onChange={(e) => setPayment(e.target.value as typeof payment)} aria-label="Ödeme">
          <option value="havale">Havale / banka kartı</option><option value="nakit">Nakit</option><option value="kredi_karti">Kredi kartı</option>
        </select>
        {payment !== "kredi_karti" && <AccountSelect id="b-acc" label="Hangi hesaptan?" value={account} onChange={setAccount} />}
      </div>
    </Modal>
  );
}

/** Kumbara alımını düzelt: kuyumcuda ödenen / alınan miktar farklıysa; varlığa ve gider kaydına da yansır. */
function EditPurchase({ jar, move, onClose }: { jar: Jar; move: Move; onClose: () => void }) {
  const ui = useUI();
  const s = jar.unit_symbol ?? sym(jar.unit);
  const [amount, setAmount] = useState(String(move.amount));
  const [cost, setCost] = useState(String(move.cost ?? ""));
  const [day, setDay] = useState(move.move_date);
  const save = useSave(() => json("PATCH", `/api/jar-moves/${move.id}`, {
    quantity: parseAmount(amount), cost: parseAmount(cost), move_date: day,
  }), () => { ui.toast("Düzeltildi"); onClose(); });
  return (
    <Modal title={`${jar.name} · alımı düzelt`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(amount) > 0) || !(parseAmount(cost) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs">Miktar ({s})
            <input className="input mt-1 text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label className="text-xs">Ödenen (₺)
            <input className="input mt-1 text-right" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
          </label>
        </div>
        <input type="date" className="input" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Alış tarihi" />
        <p className="text-xs text-stone-500">Altın ve döviz listesindeki kayıt ve ödemenin gider kaydı da güncellenir.</p>
      </div>
    </Modal>
  );
}

/** Kumbaralar: bir hedef için ayrılan para (serbest bakiyeden düşer) ya da her ay alınan altın/döviz (varlık olur). */
export default function Jars() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["jars"], queryFn: () => json<Jar[]>("GET", "/api/jars") });
  const [form, setForm] = useState<{ jar?: Jar } | null>(null);
  const [move, setMove] = useState<{ jar: Jar; dir: "yatir" | "cek" } | null>(null);
  const [buy, setBuy] = useState<Jar | null>(null);
  const [fix, setFix] = useState<{ jar: Jar; move: Move } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const patch = useSave((a: { id: number; body: object }) => json("PATCH", `/api/jars/${a.id}`, a.body));
  const del = useSave((id: number) => json("DELETE", `/api/jars/${id}`), () => ui.toast("Silindi"));
  const delMove = useSave((id: number) => json("DELETE", `/api/jar-moves/${id}`));
  const all = q.data ?? [];
  const active = all.filter((j) => !j.closed_at);
  const closed = all.filter((j) => j.closed_at);
  const total = active.filter((j) => !isUnit(j)).reduce((s, j) => s + j.balance, 0);

  return (
    <section className="card p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><Coins size={18} /> Kumbaralar</h2>
          {active.length > 0 && <p className="text-xs text-stone-500">Toplam ayrılan {tl(total)}</p>}
        </div>
        <button className="btn-ghost px-2 py-1 text-sm" onClick={() => setForm({})}><Plus size={16} /> Kumbara</button>
      </div>
      {!all.length && (
        <p className="text-sm text-stone-500">Tatil, telefon, acil durum… ya da her ay 1 gram altın, 1 çeyrek, 100 dolar. Bir şey için para ayır: hedef ve son tarih koyarsan ayda ne kadar ayırman gerektiğini ve bu hızla ne zaman ulaşacağını gösterir. Ayrılan para harcanabilir bakiyenden düşer.</p>
      )}
      <ul className="space-y-3">
        {(showClosed ? all : active).map((j) => (
          <li key={j.id} className={cx(j.closed_at && "opacity-60")}>
            <div className="flex items-baseline justify-between gap-2">
              <button className="flex items-center gap-1 text-left font-medium" onClick={() => setOpen(open === j.id ? null : j.id)}>
                {j.name}<ChevronDown size={14} className={cx("text-stone-400 transition-transform", open === j.id && "rotate-180")} />
              </button>
              {isUnit(j) ? (
                <span className="text-right text-sm tabular-nums">{qty(j.balance, j.unit_symbol ?? sym(j.unit))}
                  {j.goal ? <span className="text-stone-500"> / {qty(j.goal, j.unit_symbol ?? sym(j.unit))}</span> : null}
                  {j.value != null && j.balance > 0 && <span className="block text-xs text-stone-500">≈ {tl(j.value)}
                    {j.gain != null && <span className={j.gain >= 0 ? "text-green-700 dark:text-green-400" : "text-red-600"}> · {j.gain >= 0 ? "+" : ""}{tl(j.gain)}</span>}</span>}
                  {!!j.cost && j.balance > 0 && <span className="block text-xs text-stone-500">
                    ortalama {tl(j.cost / j.balance)}/{j.unit_symbol ?? sym(j.unit)}{j.unit_price ? ` · bugün ${tl(j.unit_price)}` : ""}</span>}
                </span>
              ) : (
                <span className="text-sm tabular-nums">{tl(j.balance)}{j.goal ? <span className="text-stone-500"> / {tl(j.goal)}</span> : null}</span>
              )}
            </div>
            {j.goal != null && (
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800">
                <div className="h-full rounded-full bg-green-600" style={{ width: `${j.pct ?? 0}%` }} />
              </div>
            )}
            <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500">
              <span>
                {j.closed_at ? `kapandı ${dateTR(j.closed_at)}`
                  : j.goal && (j.left ?? 0) <= 0 ? "Hedefe ulaştın 🎉"
                  : [j.deadline && `son tarih ${dateTR(j.deadline)}`, j.need_per_month && (isUnit(j) ? `ayda ${qty(j.need_per_month, j.unit_symbol ?? sym(j.unit))} almalısın` : `ayda ${tl(j.need_per_month)} ayırmalısın`),
                    j.monthly && (isUnit(j) ? `her ayın ${j.auto_day}. günü ${qty(j.monthly, j.unit_symbol ?? sym(j.unit))} al`
                      : `her ayın ${j.auto_day}. günü ${tl(j.monthly)} otomatik`), j.account_name && `yeri: ${j.account_name}`]
                    .filter(Boolean).join(" · ")}
                {!j.closed_at && j.goal && (j.left ?? 0) > 0 && (
                  <span className={cx("block", j.eta ? (j.on_track === false ? "text-amber-600" : "text-green-700 dark:text-green-400") : "text-stone-500")}>
                    {j.eta ? `Bu hızla (ayda ${isUnit(j) ? qty(j.pace, j.unit_symbol ?? sym(j.unit)) : tl(j.pace)}) ${monthLabel(j.eta)}'de ulaşırsın${j.on_track === false ? ", son tarihe yetişmiyor" : ""}`
                      : "Son 3 ayda ayırma yok; ayır ya da otomatik ayırmayı aç"}
                  </span>
                )}
              </span>
              {!j.closed_at && isUnit(j) && (
                <button className={cx("px-2 py-0.5", j.buy_due ? "btn-primary" : "btn-ghost border border-stone-300 dark:border-stone-700")}
                  onClick={() => setBuy(j)}><Plus size={13} /> {j.buy_due ? "Bu ayki alım: Aldım" : "Aldım"}</button>
              )}
              {!j.closed_at && !isUnit(j) && (
                <span className="flex gap-1">
                  <button className="btn-ghost border border-stone-300 px-2 py-0.5 dark:border-stone-700" onClick={() => setMove({ jar: j, dir: "yatir" })}><Plus size={13} /> Ayır</button>
                  <button className="btn-ghost border border-stone-300 px-2 py-0.5 dark:border-stone-700" disabled={j.balance <= 0}
                    onClick={() => setMove({ jar: j, dir: "cek" })}><Minus size={13} /> Çek</button>
                </span>
              )}
            </div>
            {open === j.id && (
              <div className="mt-2 space-y-2 rounded-lg bg-stone-50 p-2 text-xs dark:bg-stone-900">
                <ul className="divide-y divide-stone-200 dark:divide-stone-800">
                  {j.moves.map((m) => (
                    <li key={m.id} className="flex items-center justify-between gap-2 py-1">
                      <span>{dateTR(m.move_date)} {m.note && <span className="text-stone-500">· {m.note}</span>}</span>
                      <span className="flex items-center gap-1">
                        <span className={cx("tabular-nums", m.amount < 0 ? "text-red-600" : "text-green-700 dark:text-green-400")}>
                          {m.amount > 0 ? "+" : "−"}{isUnit(j) ? qty(Math.abs(m.amount), j.unit_symbol ?? sym(j.unit)) : tl(Math.abs(m.amount))}
                          {m.cost != null && <span className="text-stone-500"> · {tl(m.cost)}</span>}
                        </span>
                        {m.cost != null && (
                          <button className="btn-ghost p-0.5 text-stone-400 hover:text-blue-600" aria-label="Alımı düzelt"
                            onClick={() => setFix({ jar: j, move: m })}><Pencil size={12} /></button>
                        )}
                        <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Hareketi sil"
                          onClick={() => window.confirm("Bu hareket silinsin mi?") && delMove.mutate(m.id)}><Trash2 size={12} /></button>
                      </span>
                    </li>
                  ))}
                  {!j.moves.length && <li className="py-1 text-stone-500">Henüz hareket yok</li>}
                </ul>
                <div className="flex flex-wrap gap-2">
                  {!j.closed_at && <button className="btn-ghost px-2 py-1" onClick={() => setForm({ jar: j })}>Düzenle</button>}
                  <button className="btn-ghost px-2 py-1" onClick={() => patch.mutate({ id: j.id, body: { closed: !j.closed_at } })}>
                    {j.closed_at ? "Yeniden aç" : "Kapat (hedefe ulaştım / vazgeçtim)"}
                  </button>
                  <button className="btn-danger px-2 py-1" onClick={() => window.confirm(`${j.name} ve tüm hareketleri silinsin mi?`) && del.mutate(j.id)}>Sil</button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
      {closed.length > 0 && (
        <button className="btn-ghost mt-2 px-2 py-1 text-xs" onClick={() => setShowClosed(!showClosed)}>
          {showClosed ? "Kapananları gizle" : `Kapanan kumbaralar (${closed.length})`}
        </button>
      )}
      {form && <JarForm jar={form.jar} onClose={() => setForm(null)} />}
      {move && <MoveForm jar={move.jar} dir={move.dir} onClose={() => setMove(null)} />}
      {buy && <BuyForm jar={buy} onClose={() => setBuy(null)} />}
      {fix && <EditPurchase jar={fix.jar} move={fix.move} onClose={() => setFix(null)} />}
    </section>
  );
}
