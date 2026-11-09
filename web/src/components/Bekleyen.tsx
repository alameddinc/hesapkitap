// Bağlantısız kayıt kutusu ve saklanan belgeler (sahip kararı 09.11; kurallar lib/bekleyen.ts, lib/okumaKutusu.ts).
// Kabukta (App Shell) bir kez: taslak yakalayıcıyı kurar, bağlantı gelince bekleyenleri BİR KEZ gönderir (kilit), sayfanın
// üstünde "N kayıt gönderilmeyi bekliyor" / "Saklanan belge okunmayı bekliyor" şeridi ve ayrıntı penceresi.
import { onlineManager, useQueryClient } from "@tanstack/react-query";
import { CloudOff, FileClock } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { bodyOf, failureText } from "../lib/apiError";
import { anahtar, ekle, gonder, oku, yaz, type Taslak } from "../lib/bekleyen";
import { DURUM, useCevrimici } from "../lib/durum";
import { setTaslakHandler } from "../lib/idem";
import { okumaSil, okumaYukle, useBekleyenOkumalar, type BekleyenOkuma } from "../lib/okumaKutusu";
import { useUI } from "../ui";
import Modal from "./Modal";

// ---------------------------------------------------------------- taslak deposu (defter başına)
let key = anahtar(null);
let liste: Taslak[] = [];
const subs = new Set<() => void>();
const yay = () => subs.forEach((f) => f());
const depo = (): Storage | null => { try { return window.localStorage; } catch { return null; } };
function yukle(defter: string | null) {
  key = anahtar(defter);
  const d = depo();
  liste = d ? oku(d, key) : [];
  yay();
}
function kaydet(l: Taslak[]) {
  liste = l;
  const d = depo();
  if (d) yaz(d, key, l);
  yay();
}
const useTaslaklar = () => useSyncExternalStore((f) => (subs.add(f), () => { subs.delete(f); }), () => liste);

/** Taslağı tek istek olarak gönder (aynı Idempotency-Key başlıkta). */
async function tekGonder(t: Taslak) {
  try {
    const res = await fetch(t.url, { method: t.method, body: t.body, headers: t.headers });
    if (res.ok) return { ok: true as const };
    return { ok: false as const, status: res.status, message: failureText(res.status, await bodyOf(res)) };
  } catch (e) {
    return { ok: false as const, status: 0, message: (e as Error).message };
  }
}

let calisiyor: Promise<number> | null = null;
/** Bekleyenleri gönder; aynı anda yalnız bir gönderim (iki sekme/olay aynı anda tetiklese de ikinci kez gitmez). */
function gonderHepsi(zorla = false, yalniz?: string): Promise<number> {
  if (calisiyor) return calisiyor;
  calisiyor = (async () => {
    try {
      const secili = yalniz ? liste.filter((t) => t.id === yalniz) : liste;
      if (!secili.length) return 0;
      const r = await gonder(secili, tekGonder, zorla);
      // gönderim sürerken yeni taslak eklenmiş olabilir: yalnız gönderilenler çıkar, denenenler güncellenir
      const giden = new Set(secili.map((t) => t.id));
      const kalanId = new Map(r.kalan.map((t) => [t.id, t]));
      kaydet(liste.flatMap((t) => (!giden.has(t.id) ? [t] : kalanId.has(t.id) ? [kalanId.get(t.id)!] : [])));
      return r.gonderilen;
    } finally { calisiyor = null; }
  })();
  return calisiyor;
}

export default function BekleyenKutusu({ defter, onOku }: { defter: string | null; onOku: (k: BekleyenOkuma) => void }) {
  const ui = useUI();
  const qc = useQueryClient();
  const taslaklar = useTaslaklar();
  const okumalar = useBekleyenOkumalar();
  const online = useCevrimici();
  const [acik, setAcik] = useState(false);

  // defter değişince o defterin kutusu
  useEffect(() => { yukle(defter); }, [defter]);
  useEffect(() => { void okumaYukle(); }, []);
  // yakalayıcı: bağlantı yokken düşen kayıt isteği kutuya girer (lib/idem.ts withTaslak)
  useEffect(() => {
    setTaslakHandler((r) => {
      const idem = r.headers["idempotency-key"] ?? r.headers["Idempotency-Key"];
      kaydet(ekle(liste, { id: idem ?? `${Date.now()}`, method: r.method, url: r.url, body: r.body, headers: r.headers, ozet: r.ozet, at: Date.now() }));
    });
    return () => setTaslakHandler(null);
  }, []);

  const gonderVeBildir = async (zorla = false, yalniz?: string) => {
    const n = await gonderHepsi(zorla, yalniz);
    if (n > 0) { void qc.invalidateQueries(); ui.toast(DURUM.gonderildi(n)); }
  };
  // bağlantı gelince (ve açılışta bağlıysa) kendiliğinden bir kez
  useEffect(() => {
    if (onlineManager.isOnline() && liste.some((t) => !t.dur)) void gonderVeBildir();
    return onlineManager.subscribe((on) => { if (on && liste.some((t) => !t.dur)) void gonderVeBildir(); });
  }, [defter]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!taslaklar.length && !okumalar.length) return null;
  return (
    <div className="mb-3 space-y-2" data-testid="bekleyen-kutusu">
      {taslaklar.length > 0 && (
        <button className="flex min-h-11 w-full items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-left text-sm font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-100"
          onClick={() => setAcik(true)}>
          <CloudOff size={18} className="shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">{DURUM.bekleyen(taslaklar.length)}</span>
          <span className="shrink-0 text-xs font-semibold underline">{online ? DURUM.simdiDene : "Ayrıntı"}</span>
        </button>
      )}
      {okumalar.length > 0 && (
        <div className="flex min-h-11 w-full flex-wrap items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100">
          <FileClock size={18} className="shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">{online ? DURUM.okumaHazir(okumalar.length) : okumalar[0]!.tur === "fis" ? DURUM.okumaBekliyor : DURUM.okumaBekliyorDosya}</span>
          {online && <button className="btn-primary min-h-11" onClick={() => onOku(okumalar[0]!)}>{DURUM.simdiOku}</button>}
          <button className="btn-ghost min-h-11" onClick={() => void okumaSil(okumalar[0]!.id)}>Sil</button>
        </div>
      )}
      {acik && (
        <Modal title="Bekleyen kayıtlar" onClose={() => setAcik(false)}>
          <ul className="space-y-2">
            {taslaklar.map((t) => (
              <li key={t.id} className="card space-y-2 p-3 text-sm">
                <div className="font-medium">{t.ozet}</div>
                {t.hata ? <p className="text-red-700 dark:text-red-400">{t.hata}</p>
                  : <p className="text-stone-500 dark:text-stone-400">{online ? "Gönderilmeyi bekliyor" : DURUM.taslakSaklandi}</p>}
                <div className="flex flex-wrap gap-2">
                  <button className="btn-primary min-h-11" disabled={!online} onClick={() => void gonderVeBildir(true, t.id)}>{DURUM.simdiDene}</button>
                  <button className="btn-danger min-h-11" onClick={() => kaydet(liste.filter((x) => x.id !== t.id))}>{DURUM.taslakSil}</button>
                </div>
              </li>
            ))}
            {!taslaklar.length && <li className="text-sm text-stone-500">Bekleyen kayıt kalmadı.</li>}
          </ul>
        </Modal>
      )}
    </div>
  );
}
