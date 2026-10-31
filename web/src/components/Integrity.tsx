// Gece doğruluk denetimi (sunucu dc17f43): Yapılacaklar'daki "Rakamları kontrol et" maddesinin ayrıntısı.
// Her kural: ad (label), ne yapmalı (hint), kayıtlar (tarih · başlık · tutar; dokununca ilgili ekrana). "Yeniden kontrol et":
// POST /api/integrity/run (saatte 6; aşınca 429 Türkçe metin; sadece görür üye 403). Ortak defter üyesine kayıt gelmez.
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { dateTR, parseTs, tl } from "../format";
import { useUI, type Route } from "../ui";
import { useOpenRow, useSave } from "./hooks";
import Modal from "./Modal";

export interface IntegrityItem { ref: string; date: string | null; amount: number | null; title: string; route: string }
export interface IntegrityDetail { code: string; label: string; count: number; hint?: string; route?: string; items?: IntegrityItem[] }
interface RunResult { last: { day: string; ran_at: string | null } | null; details: IntegrityDetail[]; total: number; rerun?: number }

/** "2026-10-31" ya da "2026-10-31 18:02:11" (UTC) → yerel "31.10.2026" / "31.10.2026 21:02". */
function when(ts: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(ts)) return dateTR(ts);
  const d = parseTs(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

const PAGES: Route[] = ["islemler", "ekstreler", "hesaplar", "borclar", "transferler", "faturalar", "plan", "zekat"];

/** Sunucu rotası → web: islemler/m12 (elle) ve c34 (kart) düzenleme penceresini açar; ekstreler/<id> o ekstreyi açar;
 *  diğerleri sayfaya gider. */
function useGoItem(onClose: () => void) {
  const ui = useUI();
  const openRow = useOpenRow();
  return async (it: IntegrityItem) => {
    const [page, ref] = it.route.split("/");
    const route = (PAGES.includes(page as Route) ? page : "ozet") as Route;
    if (route === "islemler" && ref && /^[mc]\d+$/.test(ref) && it.date) {
      onClose();
      const ok = await openRow(ref, it.date, (row) => (row.source === "card" ? ui.openCardTxn(row) : ui.openEntry(row)));
      if (!ok) ui.error("Kayıt bulunamadı (silinmiş olabilir)");
      return;
    }
    onClose();
    if (route === "ekstreler" && ref && /^\d+$/.test(ref)) { window.location.hash = `/ekstreler?ekstre=${ref}`; window.scrollTo(0, 0); return; }
    ui.go(route);
  };
}

export default function IntegrityDialog({ details, checkedAt, rerun, onClose }: {
  details: IntegrityDetail[]; checkedAt?: string | null; rerun?: boolean; onClose: () => void;
}) {
  const ui = useUI();
  const qc = useQueryClient();
  const [data, setData] = useState<{ details: IntegrityDetail[]; at: string | null | undefined }>({ details, at: checkedAt });
  const go = useGoItem(onClose);
  const run = useSave(() => api.integrityRun() as Promise<RunResult>, (r) => {
    setData({ details: r.details, at: r.last?.ran_at ?? r.last?.day });
    void qc.invalidateQueries({ queryKey: ["reminders"] });
    ui.toast(r.total ? `${r.total} yerde tutmayan rakam var` : "Kontrol edildi: tutmayan rakam yok");
  });
  const list = data.details.filter((d) => d.count > 0);
  return (
    <Modal title="Rakamları kontrol et" onClose={onClose} footer={<>
      {rerun !== false && (
        <button className="btn-ghost border border-stone-300 dark:border-stone-700" disabled={run.isPending} onClick={() => run.mutate(undefined)}>
          <RefreshCw size={15} className={run.isPending ? "animate-spin" : undefined} aria-hidden /> {run.isPending ? "Kontrol ediliyor…" : "Yeniden kontrol et"}
        </button>
      )}
      <div className="flex-1" />
      <button className="btn-primary" onClick={onClose}>Tamam</button>
    </>}>
      <div className="space-y-4 text-sm">
        {data.at && <p className="text-xs text-stone-500">Son kontrol: {when(data.at)}</p>}
        {!list.length && <p className="text-green-700 dark:text-green-400">Tutmayan rakam kalmadı.</p>}
        {list.map((d) => (
          <section key={d.code} className="space-y-1.5">
            <h3 className="font-semibold">{d.label} <span className="font-normal text-stone-500">({d.count})</span></h3>
            {d.hint && <p className="text-stone-600 dark:text-stone-300">{d.hint}</p>}
            {d.items?.length ? (
              <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
                {d.items.map((it) => (
                  <li key={it.ref}>
                    <button className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" onClick={() => void go(it)}>
                      <span className="shrink-0 text-xs text-stone-500 tabular-nums">{it.date ? dateTR(it.date) : "—"}</span>
                      <span className="min-w-0 flex-1 break-words">{it.title}</span>
                      {it.amount != null && <span className="shrink-0 tabular-nums">{tl(it.amount)}</span>}
                      <ChevronRight size={14} className="shrink-0 text-stone-400" aria-hidden />
                    </button>
                  </li>
                ))}
                {d.count > d.items.length && <li className="px-3 py-1.5 text-xs text-stone-500">+{d.count - d.items.length} kayıt daha</li>}
              </ul>
            ) : <p className="text-xs text-stone-500">Ayrıntıyı defter sahibi görebilir.</p>}
          </section>
        ))}
      </div>
    </Modal>
  );
}
