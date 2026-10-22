import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Info, Moon, Plus, RefreshCw, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { useSave } from "../components/hooks";
import Modal from "../components/Modal";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";

interface Line { id?: number; acquired?: string | null; label: string; qty?: number; unit?: string; value: number | null; exempt?: boolean; source: string; note?: string | null }
interface Payment { id: number; year_start: string; pay_date: string; amount: number; note: string | null }
interface Person {
  id: number; name: string; is_self: boolean; plus: Line[]; minus: Line[]; gross: number; deduct: number; total: number;
  above_nisab: boolean; estimate: number; suggest_start: string | null; payments: Payment[]; missing_prices: string[];
  hawl: null | { start: string; start_hijri: string; due: string; due_hijri: string; days_left: number; progress: number;
    zakat: number; paid: number; remaining: number };
}
interface ZakatData {
  nisab: number | null; nisab_grams: number; gram_price: number | null; today_hijri: string;
  settings: { madhhab: "hanefi" | "safii"; include_app: boolean; include_receivables: boolean };
  prices: { updated: string | null; source: string | null; manual_gram: number | null; list: Record<string, number> };
  kinds: Record<string, { label: string; unit: string }>;
  people: Person[];
}

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};
const today = () => new Date().toISOString().slice(0, 10);

function AssetForm({ d, personId, onClose }: { d: ZakatData; personId: number; onClose: () => void }) {
  const ui = useUI();
  const [owner, setOwner] = useState(personId);
  const [kind, setKind] = useState("CEYREK");
  const [qty, setQty] = useState("");
  const [jewelry, setJewelry] = useState(false);
  const [note, setNote] = useState("");
  const [acquired, setAcquired] = useState("");
  const gold = kind.startsWith("ALTIN") || ["CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA", "GUMUS"].includes(kind);
  const save = useSave(() => json("POST", "/api/zakat/assets", { person_id: owner, kind, quantity: parseAmount(qty), is_jewelry: gold && jewelry, note, acquired: acquired || null }),
    () => { ui.toast("Eklendi"); onClose(); });
  return (
    <Modal title="Varlık ekle" onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(qty) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Ekle</button>}>
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="z-owner">Kimin?</label>
          <select id="z-owner" className="input" value={owner} onChange={(e) => setOwner(Number(e.target.value))}>
            {d.people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <p className="mt-1 text-xs text-stone-500">Zekât, malın sahibine düşer. Düğünde takılan takılar, aksi konuşulmadıkça kadına aittir.</p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="z-kind">Ne?</label>
            <select id="z-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(d.kinds).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="z-qty">Miktar ({d.kinds[kind]?.unit})</label>
            <input id="z-qty" className="input text-right" inputMode="decimal" autoFocus value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="z-acq">Ne zaman eline geçti? (ops.)</label>
          <input id="z-acq" type="date" className="input" max={today()} value={acquired} onChange={(e) => setAcquired(e.target.value)} />
          <p className="mt-1 text-xs text-stone-500">
            Hanefî'de nisabın üstündeyken sonradan alınan mevcut yıla katılır; tarih, zekât yılının ne zaman başladığını bulmak için
            işe yarar. Şâfiî'de her varlığın kendi yılı vardır. Tam bilmiyorsan yaklaşık ay yeter (ör. düğün tarihi).
          </p>
        </div>
        {gold && (
          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={jewelry} onChange={(e) => setJewelry(e.target.checked)} />
            <span>Takılan ziynet (bilezik, kolye…) <span className="block text-xs text-stone-500">Hanefî'de (Diyanet) zekâta dahil; Şâfiî'de mutad kullanılan ziynet muaf.</span></span>
          </label>
        )}
        <input className="input" placeholder="Not (ops., ör. düğün takısı, kasada)" value={note} onChange={(e) => setNote(e.target.value)} />
        {kind === "TRY" && (
          <p className="text-xs text-stone-500">Bu uygulamaya girdiğin banka hesapları "Ben" için zaten otomatik sayılıyor; buraya sadece başka yerde duran parayı yaz.</p>
        )}
      </div>
    </Modal>
  );
}

function PersonCard({ p, d }: { p: Person; d: ZakatData }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payAmt, setPayAmt] = useState("");
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const setStart = useSave((v: string | null) => json("PATCH", `/api/zakat/people/${p.id}`, { hawl_start: v }));
  const nextYear = useSave(() => json("POST", `/api/zakat/people/${p.id}/next-year`), () => ui.toast("Yeni zekât yılı başladı"));
  const pay = useSave(() => json("POST", `/api/zakat/people/${p.id}/payments`, { amount: parseAmount(payAmt), pay_date: today() }),
    () => { setPaying(false); setPayAmt(""); ui.toast("Ödeme kaydedildi. Allah kabul etsin."); });
  const delPay = useSave((id: number) => json("DELETE", `/api/zakat/payments/${id}`));
  const delAsset = useSave((id: number) => json("DELETE", `/api/zakat/assets/${id}`));
  const delPerson = useSave(() => json("DELETE", `/api/zakat/people/${p.id}`));
  const h = p.hawl;
  const yearPays = h ? p.payments.filter((x) => x.year_start === h.start) : [];

  return (
    <section className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{p.name}</h2>
          <p className="text-xs text-stone-500">Zekâta tabi net: <b className="tabular-nums">{tl(p.total)}</b></p>
        </div>
        <span className={cx("rounded px-2 py-0.5 text-xs", p.above_nisab ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
          : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-400")}>
          {p.above_nisab ? "Nisap üstü" : "Nisap altı"}
        </span>
      </div>

      {d.nisab != null && (
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800" title="Nisaba göre">
          <div className={cx("h-full rounded-full", p.above_nisab ? "bg-green-600" : "bg-amber-500")}
            style={{ width: `${Math.min(100, (p.total / d.nisab) * 100)}%` }} />
        </div>
      )}

      {/* zekât yılı */}
      <div className="mt-3 rounded-lg bg-stone-50 p-3 text-sm dark:bg-stone-900">
        {h ? (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span>Zekât yılı: {dateTR(h.start)} → <b>{dateTR(h.due)}</b></span>
              <span className="text-xs text-stone-500">{h.start_hijri} → {h.due_hijri}</span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
              <div className="h-full rounded-full bg-blue-600" style={{ width: `${h.progress}%` }} />
            </div>
            <div className="mt-2 text-xs text-stone-600 dark:text-stone-400">
              {h.days_left > 0 ? `${h.days_left} gün kaldı. ` : h.days_left === 0 ? "Yıl bugün doluyor. " : `Yıl ${-h.days_left} gün önce doldu. `}
              {p.above_nisab
                ? <>Bugünkü hesapla zekâtın <b>{tl(h.zakat)}</b>{h.paid > 0 && <> · ödenen {tl(h.paid)} · kalan <b>{tl(h.remaining)}</b></>}</>
                : "Şu an nisabın altındasın; yıl sonunda da altındaysan zekât düşmez."}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {paying ? (
                <>
                  <input className="input w-36 text-right" inputMode="decimal" placeholder="Tutar" autoFocus value={payAmt} onChange={(e) => setPayAmt(e.target.value)} />
                  <button className="btn-primary px-3 py-1" disabled={!(parseAmount(payAmt) > 0)} onClick={() => pay.mutate(undefined, onErr)}>Kaydet</button>
                  <button className="btn-ghost px-2 py-1" onClick={() => setPaying(false)}>Vazgeç</button>
                </>
              ) : (
                <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                  onClick={() => { setPayAmt(h.remaining > 0 ? String(h.remaining) : ""); setPaying(true); }}>Ödeme ekle</button>
              )}
              {h.days_left <= 0 && (
                <button className="btn-primary px-2 py-1 text-xs"
                  onClick={() => window.confirm("Yeni zekât yılı başlasın mı? (bu yılın ödemeleri geçmişte kalır)") && nextYear.mutate(undefined, onErr)}>
                  Yeni yılı başlat
                </button>
              )}
            </div>
            {yearPays.length > 0 && (
              <ul className="mt-2 space-y-1 text-xs">
                {yearPays.map((x) => (
                  <li key={x.id} className="flex items-center justify-between gap-2">
                    <span>{dateTR(x.pay_date)} {x.note && `· ${x.note}`}</span>
                    <span className="flex items-center gap-1 tabular-nums">{tl(x.amount)}
                      <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Ödemeyi sil"
                        onClick={() => window.confirm("Ödeme silinsin mi?") && delPay.mutate(x.id)}><Trash2 size={12} /></button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <div className="space-y-2 text-xs text-stone-600 dark:text-stone-400">
            <p>
              Zekât yılı, mal varlığının nisaba ulaştığı gün başlar ve bir kameri yıl (354 gün) sürer.
              {p.above_nisab ? " Şu an nisap üstündesin." : ""} Başladığı günü biliyorsan gir; bilmiyorsan bugünü ya da her yıl verdiğin günü (ör. Ramazan'ın 1'i) seç.
            </p>
            {p.suggest_start && (
              <div className="rounded-md border border-blue-200 bg-blue-50 p-2 dark:border-blue-900 dark:bg-blue-950/30">
                Girdiğin tarihlere göre nisaba yaklaşık <b>{dateTR(p.suggest_start)}</b>'de ulaşmışsın (bugünkü fiyatla).
                <button className="btn-primary ml-2 px-2 py-0.5 text-xs" onClick={() => setStart.mutate(p.suggest_start, onErr)}>Bu tarihten başlat</button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <input type="date" className="input w-44" max={today()} onChange={(e) => e.target.value && setStart.mutate(e.target.value, onErr)} />
              <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700" onClick={() => setStart.mutate(today(), onErr)}>Bugünden başlat</button>
            </div>
          </div>
        )}
      </div>

      {p.missing_prices.length > 0 && <p className="mt-2 text-xs text-amber-600">Fiyatı bulunamadı: {p.missing_prices.join(", ")}</p>}

      <button className="btn-ghost mt-2 px-1 py-1 text-xs" onClick={() => setOpen(!open)}>
        <ChevronDown size={14} className={cx("transition-transform", open && "rotate-180")} /> Döküm ({p.plus.length + p.minus.length} kalem)
      </button>
      {open && (
        <div className="space-y-2 text-sm">
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {p.plus.map((x, i) => (
              <li key={`p${i}`} className="flex items-center justify-between gap-2 py-1.5">
                <span className={cx("min-w-0", x.exempt && "text-stone-400")}>
                  {x.label}{x.qty != null && <span className="text-xs text-stone-500"> · {x.qty} {x.unit}</span>}
                  {x.acquired && <span className="text-xs text-stone-500"> · {dateTR(x.acquired)}</span>}
                  {x.note && <span className="text-xs text-stone-500"> · {x.note}</span>}
                  {x.source !== "varlik" && <span className="text-xs text-stone-400"> · uygulamadan</span>}
                </span>
                <span className="flex items-center gap-1 tabular-nums">{x.value != null ? tl(x.value) : "?"}
                  {x.id && <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Sil"
                    onClick={() => window.confirm(`${x.label} silinsin mi?`) && delAsset.mutate(x.id!)}><Trash2 size={12} /></button>}
                </span>
              </li>
            ))}
            {p.minus.map((x, i) => (
              <li key={`m${i}`} className="flex items-center justify-between gap-2 py-1.5">
                <span>{x.label} <span className="text-xs text-stone-400">· düşülür</span></span>
                <span className="tabular-nums text-red-600">−{tl(x.value ?? 0)}</span>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost border border-stone-300 px-2 py-1 text-xs dark:border-stone-700" onClick={() => setAdding(true)}><Plus size={14} /> Altın / döviz / para ekle</button>
            {h && <button className="btn-ghost px-2 py-1 text-xs" onClick={() => {
              const v = window.prompt("Zekât yılının başladığı gün (YYYY-AA-GG):", h.start);
              if (v) setStart.mutate(v, onErr);
            }}>Yıl başlangıcını değiştir</button>}
            {!p.is_self && <button className="btn-danger px-2 py-1 text-xs"
              onClick={() => window.confirm(`${p.name} ve varlıkları silinsin mi?`) && delPerson.mutate(undefined, onErr)}>Kişiyi sil</button>}
          </div>
        </div>
      )}
      {!open && (
        <button className="btn-ghost ml-1 px-2 py-1 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Varlık ekle</button>
      )}
      {adding && <AssetForm d={d} personId={p.id} onClose={() => setAdding(false)} />}
    </section>
  );
}

/** Zekât: kişi bazında nisap, kameri yıl, hesap ve ödeme takibi (Diyanet / Hanefî esas). */
export default function Zakat() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["zakat"], queryFn: () => json<ZakatData>("GET", "/api/zakat") });
  const [newPerson, setNewPerson] = useState("");
  const [showRules, setShowRules] = useState(false);
  const onErr = { onError: (e: Error) => ui.toast(e.message) };
  const addPerson = useSave(() => json("POST", "/api/zakat/people", { name: newPerson }), () => setNewPerson(""));
  const settings = useSave((b: object) => json("PUT", "/api/zakat/settings", b));
  const refresh = useSave(() => json("POST", "/api/zakat/prices/refresh"), () => ui.toast("Fiyatlar güncellendi"));
  const d = q.data;
  if (!d) return <p className="text-sm text-stone-500">{q.isError ? "Zekât bilgisi alınamadı" : "Yükleniyor…"}</p>;
  const pl = d.prices.list;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold"><Moon size={20} /> Zekât</h1>
        <p className="text-sm text-stone-500">Her kişi için ayrı: nisap, kameri yıl, hesap ve ödeme. Bugün {d.today_hijri}.</p>
      </div>

      <section className="card space-y-2 p-4 text-sm">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span>Nisap: <b>{d.nisab != null ? tl(d.nisab) : "?"}</b> <span className="text-xs text-stone-500">({d.nisab_grams} gr altın)</span></span>
          <span className="flex items-center gap-1 text-xs text-stone-500">
            {d.prices.manual_gram ? "elle girilen fiyat" : d.prices.updated ? `fiyatlar ${d.prices.updated.slice(11, 16)}` : "fiyat alınamadı"}
            <button className="btn-ghost p-1" aria-label="Fiyatları yenile" onClick={() => refresh.mutate(undefined, onErr)}><RefreshCw size={13} /></button>
          </span>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
          {pl.ALTIN_24 && <span>gram {tl(pl.ALTIN_24)}</span>}
          {pl.ALTIN_22 && <span>22 ayar {tl(pl.ALTIN_22)}</span>}
          {pl.CEYREK && <span>çeyrek {tl(pl.CEYREK)}</span>}
          {pl.USD && <span>$ {tl(pl.USD)}</span>}
          {pl.EUR && <span>€ {tl(pl.EUR)}</span>}
          <button className="underline decoration-dotted" onClick={() => {
            const v = window.prompt("Gram altın fiyatını elle gir (boş: piyasa fiyatı):", d.prices.manual_gram ? String(d.prices.manual_gram) : "");
            if (v != null) settings.mutate({ manual_gram: v.trim() ? parseAmount(v) : 0 }, onErr);
          }}>fiyatı elle gir</button>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-stone-100 pt-2 text-xs dark:border-stone-800">
          <label className="flex items-center gap-1">Mezhep
            <select className="input w-auto px-2 py-1 text-xs" value={d.settings.madhhab} onChange={(e) => settings.mutate({ madhhab: e.target.value }, onErr)}>
              <option value="hanefi">Hanefî (Diyanet)</option><option value="safii">Şâfiî</option>
            </select>
          </label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={d.settings.include_app}
            onChange={(e) => settings.mutate({ include_app: e.target.checked }, onErr)} /> Banka hesaplarım ve borçlarım dahil</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={d.settings.include_receivables}
            onChange={(e) => settings.mutate({ include_receivables: e.target.checked }, onErr)} /> Alacaklarım dahil</label>
        </div>
      </section>

      {d.people.map((p) => <PersonCard key={p.id} p={p} d={d} />)}

      <section className="card flex flex-wrap items-center gap-2 p-4">
        <UserPlus size={16} className="text-stone-500" />
        <input className="input min-w-0 flex-1" placeholder="Aile üyesi ekle (ör. Eşim, Annem)" value={newPerson} onChange={(e) => setNewPerson(e.target.value)} />
        <button className="btn-primary" disabled={!newPerson.trim()} onClick={() => addPerson.mutate(undefined, onErr)}>Ekle</button>
      </section>

      <section className="card p-4 text-sm">
        <button className="flex w-full items-center gap-2 text-left font-semibold" onClick={() => setShowRules(!showRules)}>
          <Info size={16} /> Nasıl hesaplanıyor?
          <ChevronDown size={14} className={cx("ml-auto text-stone-400 transition-transform", showRules && "rotate-180")} />
        </button>
        {showRules && (
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-xs text-stone-600 dark:text-stone-400">
            <li><b>Nisap:</b> 80,18 gram altın değeri. Altın, gümüş, TL, döviz ve alacaklar aynı havuzda toplanır.</li>
            <li><b>Yıl:</b> nisaba ulaştığın günden itibaren bir kameri yıl (354 gün). Nisabın yılın başında ve sonunda olması yeter; arada azalıp çoğalması önemli değil.</li>
            <li><b>Bozdurup yeniden almak:</b> yıl içinde altını paraya ya da dövize çevirip sonra yeniden alman yılı bozmaz; hepsi aynı havuz. Sadece zekâta tabi malın <b>tamamı biterse</b> yıl yeniden başlar ("Yıl başlangıcını değiştir").</li>
            <li><b>Tutar:</b> yıl dolduğu gündeki toplamın %2,5'i (kırkta biri). Hanefî'de yıl içinde eline geçen para ve altın da yıl sonundaki toplama dahildir; Şâfiî'de sonradan gelen her varlığın kendi yılı vardır, yılını doldurmayan o yıl sayılmaz.</li>
            <li><b>Kimin malı:</b> zekât sahibine düşer; ailede her kişi ayrı hesaplanır. Düğün takıları aksi konuşulmadıkça kadına aittir. Eşin zekâtını onun izniyle sen de verebilirsin.</li>
            <li><b>Ziynet:</b> Hanefî'ye (Diyanet) göre takılan altın da dahil; Şâfiî'ye göre mutad kullanılan ziynet muaf.</li>
            <li><b>Borçlar:</b> Hanefî'de vadesi bu yıl içinde dolan borçlar (kart ekstreleri, kısa vadeli borçlar) düşülür; uzun vadeli kredinin sadece bu yılki taksitleri. Şâfiî'de borç düşülmez.</li>
            <li><b>Dahil olmayanlar:</b> oturduğun ev, araba, eşyalar, yemek kartı bakiyesi.</li>
            <li><b>Ödeme:</b> erken ve parça parça verilebilir. Anne-baba, dede-nine, çocuk-torun ve eşe zekât verilmez.</li>
            <li>Kameri tarihler hesapla bulunur, resmi takvimden 1 gün sapabilir.</li>
            <li className="text-stone-500">Bilgilendirme amaçlıdır. Emin olmadığın durumda müftülüğe danış: <b>Alo Fetva 190</b>.</li>
          </ul>
        )}
      </section>
    </div>
  );
}
