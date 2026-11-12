import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Eye, Plus, Route, Undo2, X } from "lucide-react";
import { useState } from "react";
import { call } from "../lib/apiError";
import { ask } from "../lib/ask";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import SorguDurumu, { KartIskelet } from "./SorguDurumu";

/** GET /api/admin/payment-gateways (app/gateways.py): kart ekstresinde satıcının önüne yazılan ödeme aracısı önekleri.
 * Kişisel veri yok: ön ek, sayılar, en çok 5 örnek işyeri adı. */
export interface Gateway {
  prefix: string; status: "bekliyor" | "onaylandi" | "reddedildi"; source: "veri" | "elle";
  merchants: number; books: number; top_book: number; rows: number; samples: string[];
  first_seen: string | null; last_seen: string | null; decided_at: string | null;
}
export interface GatewayOverview {
  candidates: Gateway[]; approved: Gateway[]; rejected: Gateway[]; builtin: string[]; last_scan: string | null;
  thresholds: { merchants: number; books: number; single_book: number };
}
interface Preview { prefix: string; known: boolean; rows: number; merchants: number; samples: string[] }

const KEY = ["admin-payment-gateways"];
const sayi = (n: number) => n.toLocaleString("tr-TR");
const gun = (iso: string | null) => (iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("tr-TR", { day: "numeric", month: "short", year: "numeric" }) : "—");

/** Yönetici → Ödeme aracıları: gece taramasının adayları (onayla / reddet), elle ekleme, onaylı ve reddedilen liste. */
export default function PaymentGateways() {
  const ui = useUI();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, retry: false, queryFn: () => call<GatewayOverview>("GET", "/api/admin/payment-gateways") });
  const done = (r: GatewayOverview, msg: string) => { qc.setQueryData(KEY, r); ui.toast(msg); };
  const act = useSave(({ path, prefix }: { path: string; prefix: string; msg: string }) =>
    call<GatewayOverview>("POST", `/api/admin/payment-gateways${path}`, { prefix }), (r, a) => done(r, a.msg));
  const s = q.data;
  if (!s) return <SorguDurumu q={q} etiket="Ödeme aracıları yükleniyor"><KartIskelet satir={3} /></SorguDurumu>;
  const busy = act.isPending;
  return (
    <>
      <section className="card space-y-3 p-4" aria-labelledby="gw-h" data-testid="odeme-araci-adaylar">
        <h2 id="gw-h" className="flex items-center gap-2 font-semibold"><Route size={18} aria-hidden /> Ödeme aracı adayları</h2>
        <p className="text-xs text-stone-500">
          Ekstrede satıcının önüne kendi adını yazan ödeme kuruluşları ("ARACI/ORNEK KITAP"). Gece taraması bütün defterlerde
          sağ tarafta en az {s.thresholds.merchants} farklı işyeri (çoğu şehir/ilçe değil) ve {s.thresholds.books} defter ya da
          tek defterde {s.thresholds.single_book} işyeri görülen ön ekleri aday yapar. Onaylanan ön ek hemen bütün sunucuda
          ayrılır; reddedilen bir daha aday olmaz. Son tarama: {gun(s.last_scan)}.
        </p>
        {s.candidates.length === 0 ? (
          <p className="text-sm text-stone-500">Bekleyen aday yok.</p>
        ) : (
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {s.candidates.map((c) => (
              <li key={c.prefix} className="space-y-1.5 py-3 first:pt-0" data-testid="aday">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-mono text-sm font-semibold break-all">{c.prefix}</span>
                  <span className="text-xs text-stone-500 tabular-nums">{sayi(c.merchants)} işyeri · {sayi(c.books)} defter · ilk {gun(c.first_seen)}</span>
                </div>
                {c.samples.length > 0 && (
                  <p className="text-xs break-words text-stone-600 dark:text-stone-400">Örnek: {c.samples.join(" · ")}</p>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-900 tabular-nums dark:bg-amber-950 dark:text-amber-200">
                    Onaylarsan {sayi(c.rows)} satır ayrılır
                  </span>
                  <span className="ml-auto flex gap-2">
                  <button className="btn-primary inline-flex items-center gap-1 text-sm" disabled={busy}
                    onClick={() => act.mutate({ path: "/approve", prefix: c.prefix, msg: `${c.prefix} ödeme aracı olarak eklendi` })}>
                    <Check size={16} aria-hidden /> Onayla</button>
                  <button className="btn-ghost inline-flex items-center gap-1 border border-stone-300 text-sm dark:border-stone-700" disabled={busy}
                    onClick={() => void ask(`${c.prefix} reddedilsin mi? Bir daha aday olarak gelmez.`, { confirm: "Reddet", title: "Adayı reddet" })
                      .then((ok) => { if (ok) act.mutate({ path: "/reject", prefix: c.prefix, msg: `${c.prefix} reddedildi` }); })}>
                    <X size={16} aria-hidden /> Reddet</button>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <ManualAdd onAdded={(r, p) => done(r, `${p} ödeme aracı olarak eklendi`)} />
      <section className="card space-y-3 p-4" aria-labelledby="gw-ok-h" data-testid="odeme-araci-onayli">
        <h2 id="gw-ok-h" className="font-semibold">Onaylı ön ekler</h2>
        {s.approved.length === 0 ? <p className="text-sm text-stone-500">Henüz yok; yalnız sabit liste geçerli.</p> : (
          <ul className="divide-y divide-stone-100 dark:divide-stone-800">
            {s.approved.map((a) => (
              <li key={a.prefix} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <span className="font-mono text-sm font-semibold break-all">{a.prefix}</span>
                <span className="text-xs text-stone-500">{a.source === "elle" ? "elle" : "veriden"} · {gun(a.decided_at)}</span>
                <span className="grow" />
                <button className="btn-ghost inline-flex items-center gap-1 text-sm text-red-700 dark:text-red-400" disabled={busy}
                  onClick={() => void ask(`${a.prefix} listeden kaldırılsın mı? Bu önekli satırlar yeniden "${a.prefix}" adıyla toplanır; ön ek reddedilmiş sayılır.`, { confirm: "Kaldır", danger: true, title: "Ödeme aracını kaldır" })
                    .then((ok) => { if (ok) act.mutate({ path: "/reject", prefix: a.prefix, msg: `${a.prefix} kaldırıldı` }); })}>
                  <X size={16} aria-hidden /> Kaldır</button>
              </li>
            ))}
          </ul>
        )}
        {s.rejected.length > 0 && (
          <details className="text-sm" data-testid="odeme-araci-red">
            <summary className="cursor-pointer text-stone-600 dark:text-stone-300">Reddedilenler · {s.rejected.length}</summary>
            <ul className="mt-1 divide-y divide-stone-100 dark:divide-stone-800">
              {s.rejected.map((r) => (
                <li key={r.prefix} className="flex flex-wrap items-center gap-x-3 py-1.5">
                  <span className="font-mono text-sm break-all">{r.prefix}</span>
                  <span className="text-xs text-stone-500">{gun(r.decided_at)}</span>
                  <span className="grow" />
                  <button className="btn-ghost inline-flex items-center gap-1 text-sm" disabled={busy}
                    onClick={() => act.mutate({ path: "/forget", prefix: r.prefix, msg: `${r.prefix} yeniden aday olabilir` })}>
                    <Undo2 size={16} aria-hidden /> Geri al</button>
                </li>
              ))}
            </ul>
          </details>
        )}
        <details className="text-sm">
          <summary className="cursor-pointer text-stone-600 dark:text-stone-300">Sabit liste (koddaki) · {s.builtin.length}</summary>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-xs text-stone-600 dark:text-stone-400">
            {s.builtin.map((b) => <li key={b} className="whitespace-nowrap">{b}</li>)}
          </ul>
          <p className="mt-1 text-xs text-stone-500">Bu adlara 1 harf uzaklıktaki yazılış da (en az 5 harfli adlarda) aracı sayılır.</p>
        </details>
      </section>
    </>
  );
}

/** Elle ekleme: önce önizleme (kaç satır ayrılır), sonra ekle. */
function ManualAdd({ onAdded }: { onAdded: (r: GatewayOverview, prefix: string) => void }) {
  const [text, setText] = useState("");
  const [pv, setPv] = useState<Preview | null>(null);
  const look = useSave((p: string) => call<Preview>("GET", `/api/admin/payment-gateways/preview?prefix=${encodeURIComponent(p)}`), (r) => setPv(r));
  const add = useSave((p: string) => call<GatewayOverview>("POST", "/api/admin/payment-gateways", { prefix: p }),
    (r, p) => { onAdded(r, pv?.prefix ?? p); setText(""); setPv(null); });
  const val = text.trim();
  return (
    <section className="card space-y-2 p-4" aria-labelledby="gw-add-h" data-testid="odeme-araci-elle">
      <h2 id="gw-add-h" className="font-semibold">Elle ekle</h2>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (val) look.mutate(val); }}>
        <label className="sr-only" htmlFor="gw-add">Ön ek</label>
        <input id="gw-add" className="input min-w-0 flex-1 font-mono" maxLength={24} placeholder="ör. ORNEKPAY" value={text}
          onChange={(e) => { setText(e.target.value); setPv(null); }} />
        <button className="btn-ghost inline-flex items-center gap-1 border border-stone-300 dark:border-stone-700" disabled={!val || look.isPending}>
          <Eye size={16} aria-hidden /> Önizle</button>
      </form>
      {pv && (pv.known ? (
        <p className="text-sm text-stone-600 dark:text-stone-400">{pv.prefix} zaten ödeme aracı sayılıyor.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="elle-onizleme">
          <span className="min-w-0 flex-1 break-words">
            <b className="font-mono">{pv.prefix}</b>: {sayi(pv.rows)} satır ayrılır, {sayi(pv.merchants)} farklı işyeri
            {pv.samples.length > 0 && <span className="text-stone-500"> ({pv.samples.join(" · ")})</span>}
            {pv.rows > 0 && pv.merchants === 0 && <span className="block text-xs text-amber-800 dark:text-amber-300">Sağ tarafların hepsi şehir/ilçe: bu bir zincir + şube olabilir, aracı değil.</span>}
            {pv.rows === 0 && <span className="block text-xs text-stone-500">Defterlerde bu önekle satır yok; eklenirse yalnız yeni satırlarda etkili.</span>}
          </span>
          <button className="btn-primary inline-flex items-center gap-1" disabled={add.isPending} onClick={() => add.mutate(pv.prefix)}>
            <Plus size={16} aria-hidden /> Ekle</button>
        </div>
      ))}
    </section>
  );
}
