import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { useState } from "react";
import { useUI } from "../ui";
import { useMeta, useSave } from "./hooks";
import Modal from "./Modal";

export const ZAKAT_TITLE = "Zekât hesaplamayı açalım mı?";
export const ZAKAT_TEXT = "Zekât için mal varlığını (altın, döviz, nakit vb.) girersin; bu bilgiler yalnız senin hesabında, "
  + "hesaplama ve hatırlatma için kullanılır. İstediğin an kapatıp silebilirsin.";
const DETAIL = "Mal varlığı ve zekât/sadaka kayıtları dini inanca ilişkin özel nitelikli kişisel veri sayılabilir. Kimseyle "
  + "paylaşılmaz, anonim istatistiklere girmez. Özelliği kapatınca rızan geri alınır; istersen kayıtları da silersin.";

/** Zekât ve Sadaka'yı açma: ayrı onay kutusu yok. Metin gösterilir, "Aç"a basmak açık rıza eylemidir; sunucu
 * (hazır sürümde) zamanı, metni ve sürümü kaydeder. Metin sunucudan gelir (kaydedilenle aynı), yoksa yerel kopya. */
export default function ZakatConsent({ onClose, onDone }: { onClose: () => void; onDone?: () => void }) {
  const ui = useUI();
  const meta = useMeta().data;
  const status = useQuery({ queryKey: ["auth"], queryFn: () => send<any>("/api/auth/status") });
  const [more, setMore] = useState(false);
  const needs = !!meta?.module_consent?.zekat;
  const text = meta?.module_consent?.zekat ?? ZAKAT_TEXT;
  const title = meta?.module_consent_title?.zekat ?? ZAKAT_TITLE;
  const terms: string | undefined = status.data?.legal?.terms;
  let details: string | null = null;
  try { details = terms ? new URL(terms).origin + "/acik-riza" : null; } catch { /* adres bozuk */ }
  const save = useSave(() => fetch("/api/settings/modules", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modules: { zekat: true }, consent: needs ? ["zekat"] : [] }),
  }).then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Açılamadı"); }),
  () => { ui.toast("Zekât ve Sadaka açıldı"); onClose(); onDone?.(); });
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button className="btn-ghost flex-1 justify-center" onClick={onClose}>Vazgeç</button>
      <button className="btn-primary flex-1 justify-center" disabled={save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>{save.isPending ? "Açılıyor…" : "Aç"}</button>
    </>}>
      <p className="text-sm leading-relaxed text-stone-600 dark:text-stone-300">{text}</p>
      {details
        ? <a className="mt-2 inline-block text-sm text-blue-700 underline dark:text-blue-400" href={details} target="_blank" rel="noreferrer">Ayrıntılar</a>
        : <button className="mt-2 text-sm text-blue-700 underline dark:text-blue-400" aria-expanded={more} onClick={() => setMore(!more)}>Ayrıntılar</button>}
      {more && !details && <p className="mt-2 text-xs text-stone-500">{DETAIL}</p>}
    </Modal>
  );
}

/** Zekâtı kapat; istersen kayıtları da sil (rıza geri alınır). */
export function ZakatOff({ onClose }: { onClose: () => void }) {
  const ui = useUI();
  const [wipe, setWipe] = useState(false);
  const save = useSave(async () => {
    if (wipe) {
      const r = await fetch("/api/zakat/data", { method: "DELETE" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Silinemedi");
    }
    const r = await fetch("/api/settings/modules", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modules: { zekat: false } }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Kapatılamadı");
  }, () => { ui.toast(wipe ? "Kapatıldı, kayıtlar silindi" : "Zekât ve Sadaka kapatıldı"); onClose(); ui.go("diger"); });
  return (
    <Modal title="Zekât ve Sadaka kapansın mı?" onClose={onClose} footer={<>
      <button className="btn-ghost flex-1 justify-center" onClick={onClose}>Vazgeç</button>
      <button className={(wipe ? "btn-danger" : "btn-primary") + " flex-1 justify-center"} disabled={save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.error(e.message) })}>{wipe ? "Kapat ve sil" : "Kapat"}</button>
    </>}>
      <p className="text-sm text-stone-600 dark:text-stone-300">Menüden ve Özet'ten kalkar, rızan geri alınır. Tekrar açınca kayıtların yerinde olur.</p>
      <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={wipe} onChange={(e) => setWipe(e.target.checked)} />
        <span>Zekât ve sadaka kayıtlarını da sil
          <span className="block text-xs text-stone-500">Ödemeler, bağışlar, aile üyeleri ve onların varlıkları, zekât yılı ve ayarlar silinir. Kendi altın/döviz varlıkların Hesaplar'da kalır.</span></span>
      </label>
    </Modal>
  );
}
