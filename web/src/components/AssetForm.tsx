import { useState } from "react";
import { parseAmount } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Modal from "./Modal";

const today = () => new Date().toISOString().slice(0, 10);
const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
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
  const [jewelry, setJewelry] = useState(false);
  const [note, setNote] = useState("");
  const [acquired, setAcquired] = useState("");
  const [cost, setCost] = useState("");
  const gold = kind.startsWith("ALTIN") || ["CEYREK", "YARIM", "TAM", "CUMHURIYET", "ATA", "GUMUS"].includes(kind);
  const save = useSave(() => json("POST", "/api/zakat/assets", { person_id: owner, kind, quantity: parseAmount(qty), is_jewelry: gold && jewelry, note, acquired: acquired || null, cost: parseAmount(cost) > 0 ? parseAmount(cost) : null }),
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
        {kind !== "TRY" && (
          <div>
            <label className="label" htmlFor="z-cost">Toplam kaça aldın? (ops., TL)</label>
            <input id="z-cost" className="input text-right" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
            <p className="mt-1 text-xs text-stone-500">Girersen aldığından beri ne kadar kazandırdığı görünür. Hediye geldiyse boş bırak.</p>
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

