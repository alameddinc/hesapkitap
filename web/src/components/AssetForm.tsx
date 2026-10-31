import { useQuery } from "@tanstack/react-query";
import { errorText } from "../lib/apiError";
import { useState } from "react";
import { dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";

const today = () => new Date().toISOString().slice(0, 10);
const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errorText(j, r.status));
  return j as T;
};

/** Altın / döviz / para varlığı: kimin, ne, ne kadar, ne zaman, kaça. Zekât ve Net durum aynı listeyi kullanır. */
export default function AssetForm({ people, kinds, personId, onClose }: {
  people: { id: number; name: string }[]; kinds: Record<string, { label: string; unit: string }>; personId?: number; onClose: () => void;
}) {
  const ui = useUI();
  const [owner, setOwner] = useState(personId ?? people[0]?.id);
  const [kind, setKind] = useState("CEYREK");
  const [qty, setQty] = useState("");
  const [est, setEst] = useState<{ quantity: number; gram_price: number; unit_value: number; unit: string } | null>(null);
  const [estErr, setEstErr] = useState<string | null>(null);
  const [jewelry, setJewelry] = useState(false);
  const [note, setNote] = useState("");
  const [acquired, setAcquired] = useState("");
  const [cost, setCost] = useState("");
  const [finding, setFinding] = useState(false);
  const [candKind, setCandKind] = useState<"" | "nakit" | "gonderim" | "kuyumcu">("");
  const cands = useQuery({ queryKey: ["holding-candidates"], enabled: finding,
    queryFn: () => json<{ date: string; description: string; amount: number; kind: "nakit" | "gonderim" | "kuyumcu"; src: string }[]>("GET", "/api/holdings/candidates") });
  const canEstimate = ["ALTIN_24", "ALTIN_22", "ALTIN_18", "ALTIN_14", "CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA"].includes(kind);
  const gold = kind.startsWith("ALTIN") || ["CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA", "GUMUS"].includes(kind);
  const save = useSave(() => json("POST", "/api/zakat/assets", { person_id: owner, kind, quantity: parseAmount(qty), is_jewelry: gold && jewelry, note, acquired: acquired || null, cost: parseAmount(cost) > 0 ? parseAmount(cost) : null,
    estimated: !!est && parseAmount(qty) === est.quantity }),
    () => { ui.toast("Eklendi"); onClose(); });
  return (
    <Modal title="Varlık ekle" onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(qty) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Ekle</button>}>
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="z-owner">Kimin?</label>
          <select id="z-owner" className="input" value={owner} onChange={(e) => setOwner(Number(e.target.value))}>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <p className="mt-1 text-xs text-stone-500">Zekât, malın sahibine düşer. Düğünde takılan takılar, aksi konuşulmadıkça kadına aittir.</p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="z-kind">Ne?</label>
            <select id="z-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(kinds).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="z-qty">Miktar ({kinds[kind]?.unit})</label>
            <input id="z-qty" className="input text-right" inputMode="decimal" autoFocus value={qty} onChange={(e) => { setQty(e.target.value); setEst(null); }} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="z-acq">Ne zaman eline geçti? (ops.)</label>
          <div className="flex gap-2">
            <input id="z-acq" type="date" className="input min-w-0 flex-1" max={today()} value={acquired} onChange={(e) => setAcquired(e.target.value)} />
            <button type="button" className="btn-ghost shrink-0 border border-stone-300 px-2 text-xs dark:border-stone-700"
              onClick={() => setFinding(!finding)}>Çekim / gönderimlerimden bul</button>
          </div>
          {finding && (
            <div className="mt-2 flex flex-wrap gap-1 text-xs">
              {([["", "Tümü"], ["nakit", "🏧 Çekim"], ["gonderim", "↗ Gönderim"], ["kuyumcu", "💍 Kuyumcu"]] as const).map(([k, l]) => (
                <button key={k} type="button" onClick={() => setCandKind(k)}
                  className={"rounded-full border px-2 py-0.5 " + (candKind === k ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "border-stone-300 dark:border-stone-700")}>
                  {l}{cands.data ? ` (${cands.data.filter((c) => !k || c.kind === k).length})` : ""}
                </button>
              ))}
            </div>
          )}
          {finding && (
            <div className="mt-2 max-h-56 overflow-y-auto rounded-lg border border-stone-200 dark:border-stone-700">
              {cands.isLoading && <p className="p-2 text-xs text-stone-500">Aranıyor…</p>}
              {cands.data && !cands.data.length && (
                <p className="p-2 text-xs text-stone-500">Büyük nakit çekim, gönderim ya da kuyumcu harcaması bulunamadı. O döneme ait hesap dökümünü yüklersen burada çıkar.</p>
              )}
              <ul className="divide-y divide-stone-100 text-xs dark:divide-stone-800">
                {cands.data?.filter((c) => !candKind || c.kind === candKind).map((c, i) => (
                  <li key={i}>
                    <button type="button" className="flex w-full items-center justify-between gap-2 p-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50"
                      onClick={() => { setAcquired(c.date); setCost(String(c.amount)); setFinding(false); }}>
                      <span className="min-w-0">{dateTR(c.date)} · {c.kind === "kuyumcu" ? "💍 " : c.kind === "gonderim" ? "↗ " : "🏧 "}<span className="text-stone-500">{c.description}</span></span>
                      <span className="shrink-0 tabular-nums">{tl(c.amount)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-1 text-xs text-stone-500">
            Hanefî'de nisabın üstündeyken sonradan alınan mevcut yıla katılır; tarih, zekât yılının ne zaman başladığını bulmak için
            işe yarar. Şâfiî'de her varlığın kendi yılı vardır. Tam bilmiyorsan yaklaşık ay yeter (ör. düğün tarihi).
          </p>
        </div>
        {kind !== "TRY" && (
          <div>
            <label className="label" htmlFor="z-cost">Toplam kaça aldın? (ops., TL)</label>
            <input id="z-cost" className="input text-right" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
            <p className="mt-1 text-xs text-stone-500">Girersen aldığından beri ne kadar kazandırdığı görünür. Hediye geldiyse boş bırak.</p>
            {canEstimate && (
              <button type="button" className="btn-ghost mt-2 border border-stone-300 px-2 py-1 text-xs dark:border-stone-700"
                disabled={!acquired || !(parseAmount(cost) > 0)}
                onClick={async () => {
                  setEstErr(null);
                  try {
                    const r = await json<{ quantity: number; gram_price: number; unit_value: number; unit: string }>("GET",
                      `/api/holdings/estimate?kind=${kind}&cost=${parseAmount(cost)}&day=${acquired}`);
                    setEst(r); setQty(String(r.quantity).replace(".", ","));
                  } catch (e) { setEstErr((e as Error).message); }
                }}>
                {kinds[kind]?.unit === "gr" ? "Gramı" : "Adedi"} bilmiyorum, tutar ve tarihten tahmin et
              </button>
            )}
            {canEstimate && (!acquired || !(parseAmount(cost) > 0)) && (
              <p className="mt-1 text-xs text-stone-500">Tahmin için tarih ve tutar gerekli.</p>
            )}
            {estErr && <p className="mt-1 text-xs text-red-600">{estErr}</p>}
            {est && (
              <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                <b>Tahmini: {String(est.quantity).replace(".", ",")} {est.unit}.</b> O günkü en düşük altın fiyatıyla
                (24 ayar gram ≈ {tl(est.gram_price)}, %2 ihtiyat payı dahil) ve ödediğin paranın tamamı altına sayılarak
                hesaplandı; işçilik düşülmediği için gerçek miktar büyük ihtimalle biraz <b>daha az</b>. Zekâtta eksik kalmasın
                diye bilerek fazla tahmin edildi. <b>Kuyumcuda tartırıp gerçek miktarı girmen en doğrusu</b>; o zamana kadar
                listede "tahmini" diye görünür.
              </div>
            )}
          </div>
        )}
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

