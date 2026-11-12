// Kumbara katılımcıları ve aylık pay (yetenek kumbara_shares; sunucu app/jar_shares.py). Mobil:
// hesapkitap-mobile src/components/para/KumbaraPaylar.tsx (aynı sözler, src/lib/kumbaraPay.ts).
// Kişi: defterdeki kullanıcı ya da uygulamayı kullanmayan biri (yalnız ad). Her satır dokununca düzenlenir.
import { Lock, Plus, Send, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { call } from "../lib/apiError";
import { ask } from "../lib/ask";
import { cx, dateTR, parseAmount, tl } from "../format";
import { ayAdi, aySecenekleri, dagilimMetni, devirMetni, gecmisMetni, hatirlatMesaji, kisiAdi, kisiMetni, ozetMetni, tamamlanacakMetni, temelMetni,
  toplamiYapEtiketi, yetmeyenNot, type PayDurumu, type PayKisi } from "../lib/kumbaraPay";
import { useUI } from "../ui";
import { isLimited, isViewer, useBook, usePeople, useSave } from "./hooks";
import Modal from "./Modal";
import { useLock } from "./Plan";

export interface PayMove {
  id: number; move_date: string; amount: number; note: string | null; auto: number; cost: number | null;
  person_id?: number | null; outside?: number; created_by?: number | null; debt_payment_id?: number | null;
}
export interface PayJar {
  unit_symbol?: string; unit_price?: number | null; carry_over?: number;
  id: number; name: string; unit: string; balance: number; closed_at: string | null; goal: number | null; deadline: string | null;
  moves: PayMove[]; shares?: PayDurumu | null; outside?: number; share_monthly?: number | null; remind_day?: number | null;
}

const json = <T,>(method: string, url: string, body?: unknown) => call<T>(method, url, body);
const tl0 = (n: number) => tl(n).replace(/,00$/, "");
const MIKTAR = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 });
const SYM: Record<string, string> = { ALTIN_24: "gr", ALTIN_22: "gr", GUMUS: "gr", USD: "$", EUR: "€", GBP: "£" };
/** Kumbaranın biriminde biçim: ₺, "1,5 gr", "3 adet", "$100". */
export function fmtOf(j: Pick<PayJar, "unit" | "unit_symbol">): (n: number) => string {
  if ((j.unit ?? "TRY") === "TRY") return tl0;
  const s = j.unit_symbol ?? SYM[j.unit] ?? "adet";
  return s === "gr" || s === "adet" ? (n) => `${MIKTAR.format(n)} ${s}` : (n) => `${s}${MIKTAR.format(n)}`;
}
const buAy = () => new Date().toISOString().slice(0, 7);
const sonrakiAy = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + 1); return d.toISOString().slice(0, 7); };
/** Pay değişikliği ne zamandan geçerli (sunucu from_month / share_from). */
const NE_ZAMAN: [string, string][] = [["bu", "Bu aydan itibaren"], ["sonraki", "Gelecek aydan itibaren"], ["all", "Tüm aylar (geçmiş de)"]];
const fromMonth = (k: string) => (k === "all" ? "all" : k === "sonraki" ? sonrakiAy() : buAy());

/** Uygulamayı kullanmayana hatırlatma: paylaşım menüsü, yoksa WhatsApp bağlantısı (mesajı kullanıcı gönderir). */
export async function hatirlatPaylas(text: string): Promise<void> {
  const nav = navigator as Navigator & { share?: (d: { text: string }) => Promise<void> };
  if (nav.share) {
    try { await nav.share({ text }); return; } catch (e) { if ((e as Error)?.name === "AbortError") return; }
  }
  window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");
}
const TON = {
  iyi: "bg-green-100 text-green-800 dark:bg-green-900/60 dark:text-green-200",
  uyari: "bg-amber-100 text-amber-900 dark:bg-amber-900/60 dark:text-amber-200",
  notr: "",
} as const;

/** Bu sunucu katılımcıyı biliyor mu (yeni sunucu: alan var, değeri null olabilir). */
export const hasShares = (j: PayJar) => "shares" in j && ((j.unit ?? "TRY") === "TRY" || !!j.shares || "carry_over" in j);

/** Katılımcı ekleme/düzenleme sahibin ya da tam yetkilinin işi. */
export function useCanManage() {
  const b = useBook().data;
  return !isLimited(b) && !isViewer(b);
}

/** Kumbara kartında: bu ay kişi kişi pay, atılan, kalan + geçmiş. */
export function PayKarti({ j, onOpen }: { j: PayJar; onOpen: () => void }) {
  const s = j.shares;
  if (!s) return null;
  const f = fmtOf(j);
  const done = s.people.filter((p) => p.share).length;
  return (
    <div data-testid="kumbara-paylar" className="mt-1.5 rounded-lg bg-stone-50 px-2.5 py-2 dark:bg-stone-900">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <p className="text-sm font-semibold tabular-nums" data-testid="kumbara-pay-ozet">{ozetMetni(s, f)}{(j.unit ?? "TRY") !== "TRY" && j.unit_price && s.total ? <span className="font-normal text-stone-500"> (≈ {tl0(Math.round(s.total * j.unit_price))})</span> : null}</p>
        <button className="text-xs font-medium text-blue-700 hover:underline dark:text-blue-300" onClick={onOpen}>
          <Users size={12} className="mr-0.5 inline" aria-hidden="true" />Katılımcılar ({s.people.length})</button>
      </div>
      {yetmeyenNot(s, f) && <p className="text-xs text-stone-600 dark:text-stone-400" data-testid="kumbara-pay-yetmeyen">{yetmeyenNot(s, f)}</p>}
      <ul className="mt-1 divide-y divide-stone-200 dark:divide-stone-800">
        {s.people.map((p) => <KisiSatiri key={p.id} p={p} j={j} f={f} />)}
      </ul>
      {done > 0 && s.people.some((p) => p.months_counted > 0) && (
        <p className="mt-1 text-xs text-stone-500" data-testid="kumbara-pay-gecmis">
          Önceki aylar: {[...s.people, ...s.removed].filter((p) => p.months_counted > 0)
            .map((p) => `${"me" in p ? kisiAdi(p as PayKisi) : p.name} ${gecmisMetni(p)}`).join(" · ")}
        </p>
      )}
    </div>
  );
}

function KisiSatiri({ p, j, f }: { p: PayKisi; j: PayJar; f: (n: number) => string }) {
  const t = kisiMetni(p, f);
  const s = j.shares!;
  const tam = tamamlanacakMetni(p, f, s.month);
  const devir = devirMetni(p, f);
  const msg = !p.app ? hatirlatMesaji(j.name, s, p, f) : null;
  return (
    <li className="py-1 text-sm" data-testid="kumbara-pay-kisi">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{kisiAdi(p)}
          {!p.app && <span className="ml-1 text-xs font-normal text-stone-500">· uygulamada değil</span>}
          {t.rozet && <span className={cx("ml-1.5 rounded-md px-1.5 py-0.5 text-xs font-bold", TON[t.ton])}>{t.rozet}</span>}
        </span>
        <span className="text-xs tabular-nums text-stone-600 dark:text-stone-400">
          {p.state === "muaf" ? <>bu ay pay yok{p.paid ? <> · atılan {f(p.paid)}</> : null}</>
            : p.share != null ? <>pay {f(p.share)}{p.manual ? " (elle)" : ""} · bu aya {f((p.paid_for_month ?? p.paid) + (p.carried_in ?? 0))}{p.left ? <> · kalan {f(p.left)}</> : null}</>
            : <>atılan {f(p.paid)}</>}
        </span>
      </div>
      {(t.ton === "uyari" || (p.over ?? 0) > 0) && <p className="text-xs text-stone-600 dark:text-stone-400">{t.cumle}</p>}
      {devir && <p className="text-xs text-stone-600 dark:text-stone-400" data-testid="kumbara-pay-devir">{devir}</p>}
      {tam && <p className="text-xs text-stone-700 dark:text-stone-300" data-testid="kumbara-pay-tamamlanacak">{tam}</p>}
      {msg && (t.ton === "uyari" || tam) && (
        <button className="mt-0.5 inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline dark:text-blue-300"
          onClick={() => void hatirlatPaylas(msg)} aria-label={`${p.name}'e hatırlat: mesajı paylaş`}><Send size={12} aria-hidden="true" />Hatırlat</button>
      )}
    </li>
  );
}

/** Katılımcılar penceresi: liste (dokun → düzenle), ekle, ayda toplam, hatırlatma günü. */
export function KatilimciPenceresi({ j, onClose }: { j: PayJar; onClose: () => void }) {
  const ui = useUI();
  const manage = useCanManage();
  const lock = useLock("kumbara");
  const people = usePeople();
  const s = j.shares;
  const [edit, setEdit] = useState<PayKisi | "yeni" | null>(null);
  const [total, setTotal] = useState(j.share_monthly ? String(j.share_monthly).replace(".", ",") : "");
  const [day, setDay] = useState(String(j.remind_day ?? s?.remind_day ?? 25));
  const [neZaman, setNeZaman] = useState("bu");
  const [carry, setCarry] = useState(!!(s?.carry_over ?? j.carry_over));
  const f = fmtOf(j);
  const totalChanged = (parseAmount(total) > 0 ? parseAmount(total) : null) !== (j.share_monthly ?? null);
  const save = useSave(() => json("PATCH", `/api/jars/${j.id}`, {
    ...(totalChanged ? { share_monthly: parseAmount(total) > 0 ? parseAmount(total) : null, share_from: fromMonth(neZaman) } : {}),
    remind_day: Number(day), carry_over: carry,
  }), () => { ui.toast("Kaydedildi"); onClose(); });
  const editable = manage && !lock;
  // "Toplamı ₺X yap": ayda toplamı hedefin gerektirdiğine çeker; elle paylar korunur, kalan eşit bölünür
  const yap = useSave(() => json("PATCH", `/api/jars/${j.id}`, { share_monthly: s?.needed, share_from: buAy() }),
    () => { ui.toast("Toplam güncellendi"); setTotal(String(s?.needed ?? "").replace(".", ",")); });
  return (
    <Modal title={`${j.name} · katılımcılar`} onClose={onClose} footer={editable ? (
      <button className="btn-primary w-full justify-center" disabled={save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>Kaydet</button>) : undefined}>
      <div className="space-y-3">
        <p className="text-xs text-stone-500">Birden çok kişi bu kumbaraya her ay para atıyorsa ekle: herkesin aylık payı, bu ay kimin ne kadar attığı ve
          kalanı görünür. Uygulamayı kullanmayan biri için yalnız adını yazman yeter.</p>
        {lock && <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <Lock size={14} className="mt-0.5 shrink-0" aria-hidden="true" />Katılımcı eklemek ve pay değiştirmek {lock.need} paketinde. Para eklemek ve durumu görmek açık.</p>}
        {!manage && <p className="text-xs text-stone-500">Katılımcıları defter sahibi düzenler; sen kendi katkını ekleyebilirsin.</p>}
        <ul className="divide-y divide-stone-200 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-800">
          {(s?.people ?? []).map((p) => (
            <li key={p.id}>
              <button className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm disabled:cursor-default" disabled={!editable}
                onClick={() => setEdit(p)} aria-label={`${kisiAdi(p)}: düzenle`}>
                <span className="min-w-0 [overflow-wrap:anywhere]">{kisiAdi(p)}{!p.app && <span className="text-xs text-stone-500"> · uygulamada değil</span>}</span>
                <span className="shrink-0 text-xs tabular-nums text-stone-600 dark:text-stone-400">{p.state === "muaf" ? "bu ay yok" : p.share != null ? `${f(p.share)}/ay${p.manual ? " (elle)" : " (eşit)"}` : "pay yok"}</span>
              </button>
            </li>
          ))}
          {!s?.people.length && <li className="px-3 py-2 text-sm text-stone-500">Henüz katılımcı yok.</li>}
        </ul>
        {editable && <button className="btn-ghost border border-stone-300 px-2 py-1 text-sm dark:border-stone-700" onClick={() => setEdit("yeni")}>
          <Plus size={14} /> Katılımcı ekle</button>}
        {s && <p className="text-xs text-stone-500">{temelMetni(s, f)}</p>}
        {editable && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">Ayda toplam (ops.)
              <input className="input mt-1 text-right" inputMode="decimal" placeholder={s?.basis === "hedef" && s.target ? f(s.target) : "Hedeften"}
                value={total} onChange={(e) => setTotal(e.target.value)} />
            </label>
            {totalChanged && (
              <label className="col-span-2 text-xs">Değişiklik ne zamandan?
                <select className="input mt-1" value={neZaman} onChange={(e) => setNeZaman(e.target.value)}>
                  {NE_ZAMAN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </label>
            )}
            <label className="text-xs">Hatırlatma günü
              <select className="input mt-1" value={day} onChange={(e) => setDay(e.target.value)}>
                {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>Her ayın {d}. günü</option>)}
              </select>
            </label>
          </div>
        )}
        {editable && (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={carry} onChange={(e) => setCarry(e.target.checked)} />
            <span>Fazla atılan sonraki aylara sayılsın<span className="block text-xs text-stone-500">Kapalıyken fazlası o ayda kalır. Eksik her durumda devreder ve sonraki eklemeler önce onu tamamlar.</span></span>
          </label>
        )}
        {s && yetmeyenNot(s, f) && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-stone-50 p-2 text-xs text-stone-700 dark:bg-stone-900 dark:text-stone-300" data-testid="katilimci-yetmeyen">
            <span className="min-w-0 flex-1 basis-48">{yetmeyenNot(s, f)}</span>
            {editable && <button className="btn-ghost border border-stone-300 px-2 py-0.5 dark:border-stone-700" disabled={yap.isPending}
              onClick={() => yap.mutate(undefined, { onError: (e) => ui.error(e.message) })}>{toplamiYapEtiketi(s, f)}</button>}
          </div>
        )}
        <p className="text-xs text-stone-500">Payını o ay tamamlamamış uygulama kullanıcısına seçtiğin gün telefonuna bir kez hatırlatma gider
          (Ayarlar → Bildirimler → Kumbara payı). Uygulamayı kullanmayanlara bildirim gitmez; durumları yalnız burada görünür.</p>
      </div>
      {edit && <KisiFormu j={j} p={edit === "yeni" ? null : edit} people={people.people} meId={people.meId}
        taken={(s?.people ?? []).map((x) => x.user_id).filter((x): x is number => x != null)} onClose={() => setEdit(null)} />}
    </Modal>
  );
}

function KisiFormu({ j, p, people, meId, taken, onClose }: { j: PayJar; p: PayKisi | null; people: { id: number; name: string }[];
  meId?: number; taken: number[]; onClose: () => void }) {
  const ui = useUI();
  const free = people.filter((x) => !taken.includes(x.id));
  const [who, setWho] = useState<string>(p ? "" : free[0] ? `u${free[0].id}` : "ad");
  const [name, setName] = useState(p && !p.app ? p.name : "");
  const [manual, setManual] = useState(!!p?.manual);
  const [share, setShare] = useState(p?.manual && p.share ? String(p.share).replace(".", ",") : "");
  const [neZaman, setNeZaman] = useState("bu");
  const shareVal = manual && parseAmount(share) > 0 ? parseAmount(share) : null;
  const shareChanged = !p || shareVal !== (p.manual ? p.share : null);
  const save = useSave(() => p
    ? json("PATCH", `/api/jars/${j.id}/people/${p.id}`, { ...(p.app ? {} : { name: name.trim() }),
      ...(shareChanged ? { share: shareVal, from_month: fromMonth(neZaman) } : {}) })
    : json("POST", `/api/jars/${j.id}/people`, who.startsWith("u") ? { user_id: Number(who.slice(1)), share: shareVal } : { name: name.trim(), share: shareVal }),
  () => { ui.toast("Kaydedildi"); onClose(); });
  const del = useSave(() => json<{ result: string }>("DELETE", `/api/jars/${j.id}/people/${p!.id}`),
    (r) => { ui.toast(r.result === "removed" ? "Çıkarıldı; geçmişi kaldı" : "Çıkarıldı"); onClose(); });
  const needName = p ? !p.app : who === "ad";
  const ok = (!needName || name.trim().length > 0) && (!manual || shareVal != null);
  return (
    <Modal title={p ? `${kisiAdi(p)} · düzenle` : "Katılımcı ekle"} onClose={onClose} footer={
      <div className="flex w-full gap-2">
        {p && <button className="btn-danger px-3" disabled={del.isPending} aria-label="Katılımcıyı çıkar"
          onClick={() => void ask(`${kisiAdi(p)} kumbaradan çıkarılsın mı?\n\nAttığı para kumbarada kalır; geçmişi de görünür.`).then((y) => { if (y) del.mutate(undefined); })}>
          <Trash2 size={16} /></button>}
        <button className="btn-primary flex-1 justify-center" disabled={!ok || save.isPending}
          onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>Kaydet</button>
      </div>}>
      <div className="space-y-3">
        {!p && (
          <div>
            <label className="label" htmlFor="kp-who">Kim?</label>
            <select id="kp-who" className="input" value={who} onChange={(e) => setWho(e.target.value)}>
              {free.map((x) => <option key={x.id} value={`u${x.id}`}>{x.id === meId ? `Sen (${x.name})` : `${x.name} (defterde)`}</option>)}
              <option value="ad">Uygulamayı kullanmayan biri</option>
            </select>
          </div>
        )}
        {needName && (
          <div>
            <label className="label" htmlFor="kp-name">Adı</label>
            <input id="kp-name" className="input" maxLength={40} placeholder="ör. Ayşe" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        )}
        <fieldset className="space-y-1 text-sm">
          <legend className="label">Aylık pay</legend>
          <label className="flex items-center gap-2"><input type="radio" checked={!manual} onChange={() => setManual(false)} /> Eşit böl</label>
          <label className="flex items-center gap-2"><input type="radio" checked={manual} onChange={() => setManual(true)} /> Elle tutar</label>
          {manual && <input className="input text-right" inputMode="decimal" aria-label="Aylık pay" placeholder={(j.unit ?? "TRY") === "TRY" ? "Ayda ₺" : `Ayda (${j.unit_symbol ?? SYM[j.unit] ?? "adet"})`} value={share} onChange={(e) => setShare(e.target.value)} />}
        </fieldset>
        {p && shareChanged && (
          <div>
            <label className="label" htmlFor="kp-from">Değişiklik ne zamandan?</label>
            <select id="kp-from" className="input" value={neZaman} onChange={(e) => setNeZaman(e.target.value)}>
              {NE_ZAMAN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <p className="mt-1 text-xs text-stone-500">Önceki aylar o ayki payla hesaplanır; "Tüm aylar" seçilirse geçmiş de yeni payla.</p>
          </div>
        )}
        <p className="text-xs text-stone-500">Eşit bölüşte ayın toplamından elle verilen paylar düşülür, kalan eşit bölünür; bölünemeyen kalan ilk kişiye eklenir.</p>
        {p && <Muaf j={j} p={p} />}
      </div>
    </Modal>
  );
}

/** Para eklerken "kim attı" + "para nerede" (katılımcılı kumbara). */
export function KimAtti({ j, person, setPerson, outside, setOutside, label = "Kim attı?" }: { j: PayJar; person: number | null; setPerson: (n: number | null) => void;
  outside: boolean; setOutside: (b: boolean) => void; label?: string }) {
  const s = j.shares;
  if (!s?.people.length) return null;
  const sel = s.people.find((p) => p.id === person);
  return (
    <div className="space-y-2">
      <div>
        <label className="label" htmlFor="km-who">{label}</label>
        <select id="km-who" className="input" value={person ?? ""} onChange={(e) => setPerson(e.target.value ? Number(e.target.value) : null)}>
          {s.people.map((p) => <option key={p.id} value={p.id}>{kisiAdi(p)}{p.left ? ` · ${fmtOf(j)(p.left)} kaldı` : ""}</option>)}
        </select>
      </div>
      {sel && !sel.me && (j.unit ?? "TRY") === "TRY" && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={outside} onChange={(e) => setOutside(e.target.checked)} />
          <span>Para hesaplarımda değil <span className="block text-xs text-stone-500">Elden aldın ya da {sel.name} kendisinde tutuyor: harcanabilir tutarından düşmez.</span></span>
        </label>
      )}
    </div>
  );
}

/** Varsayılan "kim attı": giriş yapanın satırı, yoksa ilk kişi. */
export const defaultPerson = (j: PayJar) => j.shares?.people.find((p) => p.me)?.id ?? j.shares?.people[0]?.id ?? null;

/** TL kumbarası hareketini düzelt (dokun → düzenle): tutar, tarih, not, kim attı, para nerede. */
export function HareketDuzelt({ j, m, onClose }: { j: PayJar; m: PayMove; onClose: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState(String(Math.abs(m.amount)).replace(".", ","));
  const [day, setDay] = useState(m.move_date);
  const [note, setNote] = useState(m.note ?? "");
  const [person, setPerson] = useState<number | null>(m.person_id ?? null);
  const [outside, setOutside] = useState(!!m.outside);
  const save = useSave(() => json("PATCH", `/api/jar-moves/${m.id}`, {
    amount: parseAmount(amount), move_date: day, note, ...(m.amount > 0 && j.shares ? { person_id: person, outside } : {}),
  }), () => { ui.toast("Düzeltildi"); onClose(); });
  return (
    <Modal title={`${j.name} · ${m.amount > 0 ? "eklemeyi" : "çekimi"} düzelt`} onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(amount) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>Kaydet</button>}>
      <div className="space-y-3">
        <input className="input text-right text-lg" inputMode="decimal" aria-label="Tutar" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input type="date" className="input" aria-label="Tarih" value={day} onChange={(e) => setDay(e.target.value)} />
        <input className="input" placeholder="Not (ops.)" maxLength={120} value={note} onChange={(e) => setNote(e.target.value)} />
        {m.amount > 0 && <KimAtti j={j} person={person ?? defaultPerson(j)} setPerson={setPerson} outside={outside} setOutside={setOutside} />}
        <p className="text-xs text-stone-500">Eklendiği gün {dateTR(m.move_date)}.</p>
      </div>
    </Modal>
  );
}

/** Hareket satırında kim attı (katılımcılı kumbara). */
export function hareketKisi(j: PayJar, m: PayMove): string | null {
  if (!j.shares || m.amount <= 0) return null;
  const p = j.shares.people.find((x) => x.id === m.person_id) ?? j.shares.removed.find((x) => x.id === m.person_id);
  if (!p) return null;
  return ("me" in p ? kisiAdi(p as PayKisi) : p.name) + (m.outside ? " · elden" : "");
}

/** "Bu ay yok": kişinin bir ayı muaf (o ayki payı 0, geçmişte tam sayılır); geri alınabilir. */
function Muaf({ j, p }: { j: PayJar; p: PayKisi }) {
  const ui = useUI();
  const [ay, setAy] = useState(buAy());
  const months = p.exempt_months ?? [];
  const put = useSave((m: string) => json("PUT", `/api/jars/${j.id}/people/${p.id}/exempt/${m}`), () => ui.toast("Bu ay yok olarak işaretlendi"));
  const del = useSave((m: string) => json("DELETE", `/api/jars/${j.id}/people/${p.id}/exempt/${m}`), () => ui.toast("Geri alındı"));
  return (
    <fieldset className="space-y-2 rounded-lg border border-stone-200 p-2 dark:border-stone-800" data-testid="kumbara-muaf">
      <legend className="label px-1">Bu ay yok</legend>
      <p className="text-xs text-stone-500">Seçtiğin ay için pay alınmaz; o ay tamamlanacak oluşmaz ve geçmişte tam sayılır.</p>
      <div className="flex flex-wrap items-center gap-2">
        <select className="input w-auto" aria-label="Ay" value={ay} onChange={(e) => setAy(e.target.value)}>
          {aySecenekleri(buAy()).map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
        </select>
        <button className="btn-ghost border border-stone-300 px-2 py-1 text-sm dark:border-stone-700" disabled={!ay || months.includes(ay) || put.isPending}
          onClick={() => put.mutate(ay, { onError: (e) => ui.error(e.message) })}>İşaretle</button>
      </div>
      {months.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {months.map((m) => (
            <li key={m}><button className="rounded-full bg-stone-100 px-2 py-0.5 text-xs dark:bg-stone-800" aria-label={`${ayAdi(m)} ${m.slice(0, 4)}: geri al`}
              onClick={() => del.mutate(m, { onError: (e) => ui.error(e.message) })}>{ayAdi(m)} {m.slice(0, 4)} ✕</button></li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

/** Hareketin nereye sayıldığı (önceki aya sayılan varsa): "₺500 Ekim eksiğine, ₺200 Kasım payına sayıldı." */
export function hareketDagilim(j: PayJar, m: PayMove): string | null {
  const s = j.shares;
  return s ? dagilimMetni(s.allocations?.[String(m.id)], fmtOf(j), s.month) : null;
}
