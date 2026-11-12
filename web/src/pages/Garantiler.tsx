import { hataMetni, temizMetin } from "../lib/hataAcikla";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Camera, ChevronRight, Copy, FileText, Paperclip, Plus, ReceiptText, Search, Share2, Trash2, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import Modal from "../components/Modal";
import Segmented from "../components/Segmented";
import SorguDurumu, { SatirIskelet } from "../components/SorguDurumu";
import { isViewer, useBook } from "../components/hooks";
import { isQuotaFull, useDocQuota } from "../components/DocQuota";
import { Upgrade } from "../components/Plan";
import { cx, dateTR, parseAmount, tl, todayISO } from "../format";
import { ask } from "../lib/ask";
import { bodyOf, failureText } from "../lib/apiError";
import {
  DURUM_RENK, garanti, okumadanTaslak, servisMetni, sureYazi, SURELER,
  type BelgeTuru, type Garanti, type GarantiListe, type GarantiTaslak, type KayitliFis,
} from "../lib/garanti";
import { useUI } from "../ui";

// Garantiler (sunucu yeteneği "warranty"): ürün, alış tarihi, garanti süresi ve belgeler; bitişe 30 ve 7 gün kala
// telefona hatırlatma. #/garantiler/<id> ürünün ayrıntısı; #/garantiler?entry=<id> | ?txn=<id> "Garantiye ekle" formu.
type Filtre = "all" | "aktif" | "yakinda" | "bitti";
type Form = { mode: "new"; draft?: GarantiTaslak | null } | { mode: "edit"; g: Garanti };

const hashParts = () => {
  const [path, query] = window.location.hash.replace(/^#\/?/, "").split("?");
  return { id: Number(path.split("/")[1]) || null, q: new URLSearchParams(query ?? "") };
};
const clearHash = (id?: number | null) => window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/garantiler${id ? `/${id}` : ""}`);

export default function Garantiler() {
  const ui = useUI();
  const book = useBook();
  const viewer = isViewer(book.data);
  const [filtre, setFiltre] = useState<Filtre>("all");
  const [ara, setAra] = useState("");
  const q = useQuery({ queryKey: ["warranties", ara.trim(), filtre], queryFn: () => garanti.liste(ara, filtre), placeholderData: (p) => p });
  const [acik, setAcik] = useState<number | null>(() => hashParts().id);
  const [form, setForm] = useState<Form | null>(null);
  const [kaynak, setKaynak] = useState<"menu" | "fisler" | null>(null);
  const [okuyor, setOkuyor] = useState(false);
  const foto = useRef<HTMLInputElement>(null);
  const docq = useDocQuota();

  // "Garantiye ekle" (kayıt / kart harcaması ayrıntısından): taslakla form
  useEffect(() => {
    const { q: p } = hashParts();
    const entry = Number(p.get("entry")) || null;
    const txn = Number(p.get("txn")) || null;
    if (!entry && !txn) return;
    clearHash();
    garanti.taslak(entry ? { entry_id: entry } : { txn_id: txn! }).then((d) => setForm({ mode: "new", draft: d }), (e: Error) => ui.error(e));
  }, [ui]); // adres temizlendi: ui değişse de ikinci kez açılmaz
  useEffect(() => {
    const on = () => { const id = hashParts().id; if (id) setAcik(id); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const d = q.data;
  const kilitli = !!d?.quota.locked;
  const yeni = () => { if (kilitli) return setKaynak(null); setKaynak("menu"); };

  /** Yeni fiş fotoğrafı: mevcut okuma akışı (okuma hakkı düşer); hak doluysa fotoğraf belge olarak bağlanır, bilgiler elle. */
  async function fotoOku(f: File | undefined) {
    if (!f) return;
    setKaynak(null);
    setOkuyor(true);
    try {
      const fd = new FormData();
      fd.append("file", f, f.name || "fis.jpg");
      const res = await fetch("/api/receipts/parse", { method: "POST", body: fd });
      const body = await bodyOf(res) as Record<string, unknown> | null;
      if (isQuotaFull(body)) {
        ui.toast("Okuma hakkın doldu: fotoğraf belge olarak eklendi, bilgileri elle yaz.");
        setForm({ mode: "new", draft: { source: "photo", entry_id: null, txn_id: null, name: null, store: null, purchase_date: null,
          price: null, items: [], receipt_file: (body.receipt_file as string | null) ?? null } });
        return;
      }
      if (!res.ok || !body) throw new Error(failureText(res.status, body));
      if (body.unreadable) ui.toast(temizMetin(String(body.message ?? "")) ?? "Fotoğraf net değil; bilgileri elle yazabilirsin.");
      setForm({ mode: "new", draft: okumadanTaslak(body as Parameters<typeof okumadanTaslak>[0]) });
    } catch (e) {
      ui.error(e);
    } finally {
      setOkuyor(false);
      void docq.refresh();
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">Garantiler</h1>
          <p className="text-sm text-stone-500">Fatura ve garanti belgesi tek yerde; bitişe 30 ve 7 gün kala telefonuna hatırlatırız.</p>
        </div>
        {!viewer && (
          <button className="btn-primary" onClick={yeni} disabled={okuyor || kilitli} data-testid="garanti-ekle">
            <Plus size={16} /> {okuyor ? "Fiş okunuyor…" : "Ürün ekle"}
          </button>
        )}
      </div>

      {d?.quota.limit != null && (
        <div className={cx("rounded-lg p-3 text-sm", kilitli ? "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300")}
          data-testid="garanti-kota">
          <b>Ücretsiz pakette {d.quota.limit} ürün</b> · {d.quota.used}/{d.quota.limit} kullanıldı.
          {kilitli && <span className="mt-1 block"><Upgrade need={d.quota.need ?? "Premium"} /></span>}
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="relative sm:w-72">
          <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-stone-400" aria-hidden="true" />
          <input className="input pl-9" type="search" value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Ürün, marka, mağaza, seri no"
            aria-label="Garantilerde ara" />
        </div>
        <div className="min-w-0 overflow-x-auto sm:flex-1">
          <Segmented<Filtre> label="Durum" value={filtre} onChange={setFiltre} options={[
            { value: "all", label: `Tümü${d ? ` ${d.counts.all}` : ""}` },
            { value: "aktif", label: `Garantide${d ? ` ${d.counts.aktif}` : ""}` },
            { value: "yakinda", label: `Bitmek üzere${d ? ` ${d.counts.yakinda}` : ""}` },
            { value: "bitti", label: `Bitti${d ? ` ${d.counts.bitti}` : ""}` },
          ]} />
        </div>
      </div>

      {!d ? <SorguDurumu q={q} etiket="Garantiler yükleniyor"><SatirIskelet n={4} /></SorguDurumu> : (
        <Liste d={d} filtre={filtre} ara={ara} onOpen={(g) => { setAcik(g.id); clearHash(g.id); }} onAdd={viewer || kilitli ? undefined : yeni} />
      )}

      <input ref={foto} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { void fotoOku(e.target.files?.[0]); e.target.value = ""; }} />
      {kaynak === "menu" && (
        <Modal title="Ürün ekle" onClose={() => setKaynak(null)}>
          <div className="space-y-2">
            <Kaynak icon={<Plus size={18} />} title="Elle gir" sub="Ürün adı, alış tarihi, garanti süresi" onClick={() => { setKaynak(null); setForm({ mode: "new" }); }} />
            <Kaynak icon={<ReceiptText size={18} />} title="Kayıtlı fişten" sub="Okunmuş bir fişten ürünü seç; fiş fotoğrafı belge olur" onClick={() => setKaynak("fisler")} />
            <Kaynak icon={<Camera size={18} />} title="Fiş / fatura fotoğrafı çek" sub="Okunur ve form dolar (1 okuma hakkı)" onClick={() => foto.current?.click()} />
          </div>
        </Modal>
      )}
      {kaynak === "fisler" && (
        <FisSec onClose={() => setKaynak(null)} onPick={(f) => {
          setKaynak(null);
          garanti.taslak({ entry_id: f.entry_id }).then((dr) => setForm({ mode: "new", draft: dr }), (e: Error) => ui.error(e));
        }} />
      )}
      {form && <GarantiForm form={form} d={d} onClose={() => setForm(null)} onSaved={(g) => { setForm(null); setAcik(g.id); clearHash(g.id); }} />}
      {acik != null && !form && <Ayrinti id={acik} d={d} onClose={() => { setAcik(null); clearHash(); }} onEdit={(g) => setForm({ mode: "edit", g })} />}
    </div>
  );
}

function Kaynak({ icon, title, sub, onClick }: { icon: React.ReactNode; title: string; sub: string; onClick: () => void }) {
  return (
    <button className="flex w-full items-center gap-3 rounded-lg border border-stone-200 p-3 text-left hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800" onClick={onClick}>
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300">{icon}</span>
      <span className="min-w-0 flex-1"><span className="block font-medium">{title}</span><span className="block text-xs text-stone-500">{sub}</span></span>
      <ChevronRight size={16} className="shrink-0 text-stone-400" />
    </button>
  );
}

function Rozet({ g }: { g: Garanti }) {
  return <span className={cx("inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", DURUM_RENK[g.status])}>{g.status_label}</span>;
}

function Liste({ d, filtre, ara, onOpen, onAdd }: { d: GarantiListe; filtre: Filtre; ara: string; onOpen: (g: Garanti) => void; onAdd?: () => void }) {
  if (!d.items.length) {
    const bos = !d.counts.all;
    return (
      <div className="card p-6 text-center">
        <BadgeCheck size={28} className="mx-auto text-stone-400" />
        <p className="mt-2 font-medium">{bos ? "Henüz ürün yok" : ara.trim() ? "Aramaya uyan ürün yok" : filtre === "yakinda" ? "Garantisi bitmek üzere ürün yok" : filtre === "bitti" ? "Garantisi biten ürün yok" : "Bu durumda ürün yok"}</p>
        {bos && <p className="mt-1 text-sm text-stone-500">Beyaz eşya, telefon, kulaklık… Faturasını ve garanti belgesini ekle; arıza olunca aramakla uğraşma.</p>}
        {bos && onAdd && <button className="btn-primary mt-3" onClick={onAdd}><Plus size={16} /> Ürün ekle</button>}
      </div>
    );
  }
  return (
    <ul className="card divide-y divide-stone-100 dark:divide-stone-800" data-testid="garanti-liste">
      {d.items.map((g) => (
        <li key={g.id}>
          <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => onOpen(g)}>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{g.name}</span>
                <Rozet g={g} />
              </span>
              <span className="mt-0.5 block text-xs text-stone-500 [overflow-wrap:anywhere]">
                {[[g.brand, g.model].filter(Boolean).join(" "), g.store, `Alış ${dateTR(g.purchase_date)}`].filter(Boolean).join(" · ")}
              </span>
            </span>
            <span className="shrink-0 text-right">
              <span className={cx("block text-sm font-medium tabular-nums", g.status === "yakinda" && "text-amber-700 dark:text-amber-300", g.status === "bitti" && "text-stone-500")}>{g.left_text}</span>
              <span className="block text-xs text-stone-500 tabular-nums">{g.status === "bitti" ? "Bitti " : "Bitiş "}{dateTR(g.ends_at)}</span>
            </span>
            {g.files.length > 0 && <span className="flex shrink-0 items-center gap-0.5 text-xs text-stone-400" aria-label={`${g.files.length} belge`}><Paperclip size={14} />{g.files.length}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function FisSec({ onClose, onPick }: { onClose: () => void; onPick: (f: KayitliFis) => void }) {
  const [ara, setAra] = useState("");
  const q = useQuery({ queryKey: ["warranty-receipts", ara.trim()], queryFn: () => garanti.fisler(ara), placeholderData: (p) => p });
  return (
    <Modal title="Kayıtlı fişten" onClose={onClose}>
      <div className="space-y-3">
        <input className="input" type="search" value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Mağaza ya da ürün ara" aria-label="Fişlerde ara" autoFocus />
        {!q.data ? <SorguDurumu q={q} etiket="Fişler yükleniyor"><SatirIskelet n={3} kart={false} /></SorguDurumu>
          : !q.data.length ? <p className="text-sm text-stone-500">{ara.trim() ? "Aramaya uyan fiş yok." : "Okunmuş fiş yok. Fiş fotoğrafı çekerek de ekleyebilirsin."}</p>
          : (
            <ul className="divide-y divide-stone-100 dark:divide-stone-800">
              {q.data.map((f) => (
                <li key={f.entry_id}>
                  <button className="flex w-full items-center gap-3 py-2.5 text-left" onClick={() => onPick(f)}>
                    <ReceiptText size={18} className="shrink-0 text-stone-400" />
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium [overflow-wrap:anywhere]">{f.store}</span>
                      <span className="block text-xs text-stone-500 [overflow-wrap:anywhere]">{dateTR(f.date)}{f.items ? ` · ${f.items} kalem` : ""}{f.preview.length ? ` · ${f.preview.join(", ")}` : ""}</span>
                    </span>
                    <span className="shrink-0 text-sm tabular-nums">{tl(f.amount)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
      </div>
    </Modal>
  );
}

function GarantiForm({ form, d, onClose, onSaved }: { form: Form; d?: GarantiListe; onClose: () => void; onSaved: (g: Garanti) => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const g = form.mode === "edit" ? form.g : null;
  const dr = form.mode === "new" ? form.draft ?? null : null;
  const def = d?.default_months ?? 24;
  const [name, setName] = useState(g?.name ?? dr?.name ?? "");
  const [brand, setBrand] = useState(g?.brand ?? dr?.items.find((i) => i.name === dr?.name)?.brand ?? "");
  const [model, setModel] = useState(g?.model ?? "");
  const [store, setStore] = useState(g?.store ?? dr?.store ?? "");
  const [date, setDate] = useState(g?.purchase_date ?? dr?.purchase_date ?? todayISO());
  const [price, setPrice] = useState(g?.price != null ? String(g.price).replace(".", ",") : dr?.price != null ? String(dr.price).replace(".", ",") : "");
  const [months, setMonths] = useState(String(g?.months ?? def));
  const [extra, setExtra] = useState(String(g?.extra_months ?? 0));
  const [serial, setSerial] = useState(g?.serial_no ?? "");
  const [note, setNote] = useState(g?.note ?? "");
  const [files, setFiles] = useState<{ f: File; t: BelgeTuru }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pick = useRef<HTMLInputElement>(null);
  const items = dr?.items ?? [];
  const dirty = form.mode === "new" ? !!(name || serial || note || files.length) : name !== g!.name || note !== (g!.note ?? "") || serial !== (g!.serial_no ?? "");

  async function kaydet() {
    setErr(null);
    if (!name.trim() || !date) return setErr("Ürün adı ve alış tarihi gerekli");
    const m = Number(months), e = Number(extra || 0);
    if (!Number.isInteger(m) || m < 0 || m > 240 || !Number.isInteger(e) || e < 0 || e > 240) return setErr("Garanti süresi 0–240 ay olmalı");
    const body = { name: name.trim(), brand: brand.trim() || null, model: model.trim() || null, store: store.trim() || null, purchase_date: date,
      price: price.trim() ? parseAmount(price) : null, months: m, extra_months: e, serial_no: serial.trim() || null, note: note.trim() || null };
    setBusy(true);
    try {
      let out = g ? await garanti.duzenle(g.id, body)
        : await garanti.ekle({ ...body, entry_id: dr?.entry_id ?? null, txn_id: dr?.txn_id ?? null, receipt_file: dr?.source === "photo" ? dr.receipt_file : null });
      for (const x of files) {
        try { out = await garanti.belgeEkle(out.id, x.f, x.f.name || "belge", x.t); }
        catch (e2) { ui.error(`${x.f.name}: ${hataMetni(e2)}`); }
      }
      void qc.invalidateQueries({ queryKey: ["warranties"] });
      ui.toast(g ? "Kaydedildi" : "Garantilere eklendi");
      onSaved(out);
    } catch (e2) {
      setErr(hataMetni(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={g ? "Ürünü düzenle" : "Garantiye ekle"} onClose={onClose} dirty={dirty && !busy} onSave={kaydet} footer={
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" onClick={() => void kaydet()} disabled={busy}>{busy ? "Kaydediliyor…" : "Kaydet"}</button>
      </div>}>
      <div className="space-y-3">
        {dr && dr.source !== "photo" && (
          <p className="rounded-lg bg-blue-50 p-2 text-xs text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
            {dr.source === "entry" ? "Kayıtlı fişten" : "Kart harcamasından"}: {dr.store}{dr.purchase_date ? `, ${dateTR(dr.purchase_date)}` : ""}
            {dr.receipt_file ? ". Fiş fotoğrafı belge olarak eklenir." : "."}{dr.installments ? ` ${dr.installments} taksit: fiyat toplam tutardır.` : ""}
          </p>
        )}
        {dr?.source === "photo" && dr.receipt_file && <p className="rounded-lg bg-blue-50 p-2 text-xs text-blue-900 dark:bg-blue-950/40 dark:text-blue-200">Fiş fotoğrafı belge olarak eklenir.</p>}
        {items.length > 1 && (
          <div>
            <div className="label">Hangi ürün? (fişteki kalemler)</div>
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Fişteki kalemler">
              {items.map((i, n) => (
                <button key={n} type="button" role="radio" aria-checked={name === i.name}
                  className={cx("hit max-w-full rounded-full border px-2.5 py-1 text-left text-xs [overflow-wrap:anywhere]", name === i.name ? "border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200" : "border-stone-300 dark:border-stone-700")}
                  onClick={() => { setName(i.name); setPrice(String(i.amount).replace(".", ",")); if (i.brand) setBrand(i.brand); }}>
                  {i.name} · {tl(i.amount)}
                </button>
              ))}
            </div>
          </div>
        )}
        <div><label className="label" htmlFor="g-ad">Ürün adı</label>
          <input id="g-ad" className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="ör. Bulaşık makinesi" /></div>
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label" htmlFor="g-marka">Marka (isteğe bağlı)</label><input id="g-marka" className="input" value={brand} onChange={(e) => setBrand(e.target.value)} maxLength={80} /></div>
          <div><label className="label" htmlFor="g-model">Model (isteğe bağlı)</label><input id="g-model" className="input" value={model} onChange={(e) => setModel(e.target.value)} maxLength={80} /></div>
        </div>
        <div><label className="label" htmlFor="g-magaza">Mağaza</label><input id="g-magaza" className="input" value={store} onChange={(e) => setStore(e.target.value)} maxLength={120} /></div>
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label" htmlFor="g-tarih">Alış tarihi</label><input id="g-tarih" className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></div>
          <div><label className="label" htmlFor="g-fiyat">Fiyat (₺)</label><input id="g-fiyat" className="input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0,00" /></div>
        </div>
        <div>
          <label className="label" htmlFor="g-sure">Garanti süresi (ay)</label>
          <div className="flex flex-wrap items-center gap-1.5">
            <input id="g-sure" className="input w-20" inputMode="numeric" value={months} onChange={(e) => setMonths(e.target.value.replace(/\D/g, ""))} />
            {SURELER.map((s) => (
              <button key={s} type="button" aria-pressed={Number(months) === s} onClick={() => setMonths(String(s))}
                className={cx("hit rounded-full border px-2.5 py-1 text-xs", Number(months) === s ? "border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200" : "border-stone-300 dark:border-stone-700")}>{sureYazi(s)}</button>
            ))}
          </div>
          <p className="mt-1 text-xs text-stone-500">Türkiye'de tüketici ürünlerinde yasal garanti en az 2 yıldır; belgendeki süreyi yaz.</p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label" htmlFor="g-ek">Ek garanti (ay)</label><input id="g-ek" className="input" inputMode="numeric" value={extra} onChange={(e) => setExtra(e.target.value.replace(/\D/g, ""))} /></div>
          <div><label className="label" htmlFor="g-seri">Seri no (isteğe bağlı)</label><input id="g-seri" className="input" value={serial} onChange={(e) => setSerial(e.target.value)} maxLength={80} /></div>
        </div>
        <div><label className="label" htmlFor="g-not">Not</label><textarea id="g-not" className="input min-h-16" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="ör. Servis numarası, kutusu nerede" /></div>
        {!g && (
          <div className="space-y-1.5">
            <div className="label">Belgeler (fatura, garanti belgesi; fotoğraf ya da PDF)</div>
            {files.map((x, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <FileText size={16} className="shrink-0 text-stone-400" /><span className="min-w-0 flex-1 truncate">{x.f.name}</span>
                <select className="input w-auto px-2 py-1 text-sm" value={x.t} aria-label="Belge türü" onChange={(e) => setFiles((l) => l.map((y, j) => (j === i ? { ...y, t: e.target.value as BelgeTuru } : y)))}>
                  <option value="fatura">Fatura / fiş</option><option value="garanti">Garanti belgesi</option><option value="diger">Diğer</option>
                </select>
                <button className="btn-ghost p-1.5" aria-label={`${x.f.name} kaldır`} onClick={() => setFiles((l) => l.filter((_, j) => j !== i))}><Trash2 size={15} /></button>
              </div>
            ))}
            {files.length + (dr?.receipt_file ? 1 : 0) < (d?.max_files ?? 5) && (
              <button type="button" className="btn-ghost -ml-2" onClick={() => pick.current?.click()}><Paperclip size={15} /> Belge ekle</button>
            )}
            <input ref={pick} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => {
              const l = [...(e.target.files ?? [])].map((f) => ({ f, t: (files.length || dr?.receipt_file ? "garanti" : "fatura") as BelgeTuru }));
              setFiles((x) => [...x, ...l].slice(0, (d?.max_files ?? 5) - (dr?.receipt_file ? 1 : 0)));
              e.target.value = "";
            }} />
          </div>
        )}
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}

function Ayrinti({ id, d, onClose, onEdit }: { id: number; d?: GarantiListe; onClose: () => void; onEdit: (g: Garanti) => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const fromList = d?.items.find((x) => x.id === id);
  const q = useQuery({ queryKey: ["warranties", "one", id], queryFn: () => fetch(`/api/warranties/${id}`).then(async (r) => {
    const b = await bodyOf(r);
    if (!r.ok) throw new Error(failureText(r.status, b));
    return b as Garanti;
  }), initialData: fromList, retry: false });
  const g = q.data;
  const [tur, setTur] = useState<BelgeTuru>("garanti");
  const [busy, setBusy] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const tazele = (x?: Garanti) => { if (x) qc.setQueryData(["warranties", "one", id], x); void qc.invalidateQueries({ queryKey: ["warranties"] }); };
  const paylasilabilir = useMemo(() => typeof navigator !== "undefined" && "share" in navigator, []);

  if (!g) return (
    <Modal title="Ürün" onClose={onClose}>
      {q.error ? <p className="text-sm text-stone-600 dark:text-stone-300">{hataMetni(q.error)}</p> : <SatirIskelet n={3} kart={false} />}
    </Modal>
  );

  async function belgeEkle(fl: FileList | null) {
    if (!fl?.length || !g) return;
    setBusy(true);
    try {
      let out: Garanti | undefined;
      for (const f of [...fl]) out = await garanti.belgeEkle(g.id, f, f.name || "belge", tur);
      tazele(out);
      ui.toast("Belge eklendi");
    } catch (e) { ui.error(e); tazele(); } finally { setBusy(false); }
  }
  async function belgeSil(fid: number) {
    if (!g || !(await ask("Bu belge kaldırılsın mı?", { confirm: "Kaldır", danger: true }))) return;
    try { tazele(await garanti.belgeSil(g.id, fid)); ui.toast("Belge kaldırıldı"); } catch (e) { ui.error(e); }
  }
  async function sil() {
    if (!g || !(await ask(`“${g.name}” garantilerden silinsin mi?`, { confirm: "Sil", danger: true, irreversible: true, title: "Ürün silinsin mi?" }))) return;
    try { await garanti.sil(g.id); tazele(); ui.toast("Ürün silindi"); onClose(); } catch (e) { ui.error(e); }
  }
  const metin = servisMetni(g, dateTR, tl);
  async function paylas() {
    if (!g) return;
    try {
      const blobs = await Promise.all(g.files.map(async (f) => {
        const r = await fetch(garanti.belgeAdresi(g.id, f.id));
        const b = await r.blob();
        return new File([b], `${g.name}-${f.doc_label}-${f.id}${f.pdf ? ".pdf" : ".jpg"}`.replace(/[\\/:*?"<>|]+/g, "-"), { type: b.type });
      }));
      const data: ShareData = { title: `${g.name} garanti belgeleri`, text: metin, files: blobs };
      if (paylasilabilir && (!navigator.canShare || navigator.canShare(data))) await navigator.share(data);
      else if (paylasilabilir) await navigator.share({ title: data.title, text: metin });
      else for (const f of g.files) window.open(garanti.belgeAdresi(g.id, f.id, true), "_blank", "noopener");
    } catch (e) { if ((e as Error).name !== "AbortError") ui.error(e); }
  }

  return (
    <Modal title={g.name} onClose={onClose} footer={g.can_edit ? (
      <div className="flex justify-between gap-2">
        <button className="btn-danger" onClick={() => void sil()}><Trash2 size={15} /> Sil</button>
        <button className="btn-primary" onClick={() => onEdit(g)}>Düzenle</button>
      </div>) : undefined}>
      <div className="space-y-4" data-testid="garanti-ayrinti">
        <div className="flex flex-wrap items-center gap-2">
          <Rozet g={g} />
          <span className={cx("text-lg font-semibold tabular-nums", g.status === "yakinda" && "text-amber-700 dark:text-amber-300")}>{g.left_text}</span>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          {(g.brand || g.model) && <><dt className="text-stone-500">Marka / model</dt><dd className="[overflow-wrap:anywhere]">{[g.brand, g.model].filter(Boolean).join(" ")}</dd></>}
          {g.store && <><dt className="text-stone-500">Mağaza</dt><dd className="[overflow-wrap:anywhere]">{g.store}</dd></>}
          <dt className="text-stone-500">Alış</dt><dd className="tabular-nums">{dateTR(g.purchase_date)}{g.price != null ? ` · ${tl(g.price)}` : ""}</dd>
          <dt className="text-stone-500">Garanti</dt><dd>{sureYazi(g.months)}{g.extra_months ? ` + ${sureYazi(g.extra_months)} ek` : ""}</dd>
          <dt className="text-stone-500">Bitiş</dt><dd className="tabular-nums">{dateTR(g.ends_at)}</dd>
          {g.serial_no && <><dt className="text-stone-500">Seri no</dt><dd className="[overflow-wrap:anywhere] font-mono text-xs leading-5">{g.serial_no}</dd></>}
          {g.note && <><dt className="text-stone-500">Not</dt><dd className="whitespace-pre-wrap [overflow-wrap:anywhere]">{g.note}</dd></>}
        </dl>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Belgeler</h3>
          {!g.files.length && <p className="text-sm text-stone-500">Belge yok. Faturanın ya da garanti belgesinin fotoğrafını ekle.</p>}
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {g.files.map((f) => (
              <li key={f.id} className="relative overflow-hidden rounded-lg border border-stone-200 dark:border-stone-700">
                <a href={garanti.belgeAdresi(g.id, f.id)} target="_blank" rel="noopener" className="block">
                  {f.pdf ? <div className="flex h-24 items-center justify-center bg-stone-50 dark:bg-stone-800"><FileText size={28} className="text-stone-400" /></div>
                    : <img src={garanti.belgeAdresi(g.id, f.id)} alt={f.doc_label} className="h-24 w-full object-cover" loading="lazy" />}
                  <span className="block truncate px-2 py-1 text-xs">{f.doc_label}{f.from_receipt ? " (fiş)" : ""}</span>
                </a>
                {g.can_edit && <button className="absolute top-1 right-1 rounded-full bg-white/90 p-1 text-stone-600 shadow-sm dark:bg-stone-900/90 dark:text-stone-300" aria-label={`${f.doc_label} kaldır`} onClick={() => void belgeSil(f.id)}><Trash2 size={14} /></button>}
              </li>
            ))}
          </ul>
          {g.can_edit && g.files.length < (d?.max_files ?? 5) && (
            <div className="flex flex-wrap items-center gap-2">
              <select className="input w-auto px-2 py-1 text-sm" value={tur} onChange={(e) => setTur(e.target.value as BelgeTuru)} aria-label="Eklenecek belgenin türü">
                <option value="garanti">Garanti belgesi</option><option value="fatura">Fatura / fiş</option><option value="diger">Diğer</option>
              </select>
              <button className="btn-ghost" onClick={() => pick.current?.click()} disabled={busy}><Paperclip size={15} /> {busy ? "Ekleniyor…" : "Belge ekle"}</button>
              <input ref={pick} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => { void belgeEkle(e.target.files); e.target.value = ""; }} />
            </div>
          )}
        </section>

        <section className="space-y-2 rounded-lg bg-stone-50 p-3 dark:bg-stone-800/60">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Wrench size={15} /> Arıza / servis</h3>
          <p className="text-xs text-stone-500">{g.status === "bitti" ? "Garanti bitmiş; yine de fatura servis ücretinde işine yarayabilir." : "Servise ya da mağazaya bu bilgileri ve belgeleri gönder."}</p>
          <pre className="whitespace-pre-wrap rounded-md bg-white p-2 font-sans text-xs [overflow-wrap:anywhere] dark:bg-stone-900">{metin}</pre>
          <div className="flex flex-wrap gap-2">
            {g.files.length > 0 && <button className="btn-primary" onClick={() => void paylas()}><Share2 size={15} /> Belgeleri paylaş</button>}
            <button className="btn-ghost" onClick={() => void navigator.clipboard?.writeText(metin).then(() => ui.toast("Kopyalandı"), () => ui.error("Kopyalanamadı"))}><Copy size={15} /> Bilgileri kopyala</button>
          </div>
        </section>
      </div>
    </Modal>
  );
}
