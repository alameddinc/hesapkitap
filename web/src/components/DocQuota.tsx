import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, FileWarning } from "lucide-react";
import { useUI } from "../ui";
import { useMeta } from "./hooks";
import Modal from "./Modal";

/** GET /api/me/ai. unit "docs": hazır sürüm, aylık belge okuma hakkı (ailede ortak havuz); "ai": kendi sunucusu, yapay zekâ kotası.
 *  Yönetici: used/quota null. */
export interface AiUsage {
  used: number | null;
  quota: number | null;
  own_key: boolean;
  unit?: "docs" | "ai";
  shared_available?: boolean;
}

/** Hak doldu (sunucu reddi): 502/422 gövdesi ya da yükleme sonucu. receipt_file: fiş fotoğrafı okunmadan saklandı. */
export interface QuotaFull {
  manual: true;
  quota_full: true;
  limit?: number;
  upgrade?: boolean;
  detail?: string;
  message?: string;
  receipt_file?: string | null;
}

export const PREMIUM_DOCS = 1000;

export const isQuotaFull = (j: unknown): j is QuotaFull =>
  !!j && typeof j === "object" && (j as { quota_full?: unknown }).quota_full === true;

/** fetch dışı akışlar (throw) için: hata nesnesi gövdeyi taşır. */
export class QuotaFullError extends Error {
  info: QuotaFull;
  constructor(info: QuotaFull) {
    super(info.detail ?? info.message ?? "Bu ayki belge okuma hakkın doldu");
    this.info = info;
  }
}

/** Bir sonraki ayın 1'i: "1 Kasım". */
export function renewDate(today = new Date()): string {
  return new Date(today.getFullYear(), today.getMonth() + 1, 1).toLocaleDateString("tr-TR", { day: "numeric", month: "long" });
}

export function useDocQuota() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me-ai"], staleTime: 30_000,
    queryFn: () => fetch("/api/me/ai").then((r) => (r.ok ? r.json() : null) as Promise<AiUsage | null>),
  });
  const d = q.data;
  const docs = d?.unit === "docs" && d.used != null && d.quota != null;
  return {
    data: d,
    /** hazır sürüm ve hak bitti: dosya seçici açılmaz */
    full: docs && d!.used! >= d!.quota!,
    /** okuma ya da ret sonrası sayacı tazele */
    refresh: () => qc.invalidateQueries({ queryKey: ["me-ai"] }),
  };
}

/** Paket sayfası: Ayarlar → Paketin. */
export function useGoPlan() {
  const ui = useUI();
  return () => {
    ui.go("ayarlar");
    setTimeout(() => document.getElementById("paket")?.scrollIntoView({ behavior: "smooth", block: "start" }), 150);
  };
}

/** "Bu ayki belge okuma hakkın doldu" uyarısı. manual: birincil düğmeler (Elle ekle / Elle gider + Elle gelir). */
export function QuotaFullModal({ onClose, manual, upgrade, hasPhoto }: {
  onClose: () => void;
  manual: { label: string; onClick: () => void }[];
  upgrade?: boolean; // ücretsiz pakette: Premium bağlantısı
  hasPhoto?: boolean; // fiş fotoğrafı saklandı, forma ekli gider
}) {
  const plan = useMeta().data?.plan;
  const goPlan = useGoPlan();
  const showUpgrade = upgrade ?? plan?.plan === "free";
  return (
    <Modal title="Bu ayki belge okuma hakkın doldu" onClose={onClose} footer={<>
      {manual.map((m, i) => (
        <button key={m.label} className={i === 0 ? "btn-primary flex-1 justify-center" : "btn-ghost flex-1 justify-center border border-stone-300 dark:border-stone-700"}
          onClick={m.onClick} autoFocus={i === 0}>{m.label}</button>
      ))}
    </>}>
      <div className="flex items-start gap-3 text-sm">
        <FileWarning size={22} className="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
        <div className="space-y-1">
          <p>Ay başında yenilenir ({renewDate()}). Bu arada harcamanı elle ekleyebilirsin.</p>
          {hasPhoto && <p className="text-xs text-stone-500">Fotoğraf okunmadan saklandı; elle eklediğin gidere ekli gelir.</p>}
          {showUpgrade && (
            <button className="btn-ghost mt-1 border border-amber-300 px-3 py-1.5 text-sm text-amber-800 dark:border-amber-800 dark:text-amber-300"
              onClick={() => { onClose(); goPlan(); }}>
              <Crown size={14} aria-hidden="true" /> Premium: ayda {PREMIUM_DOCS}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Ayarlar: hazır sürümde kalan belge okuma hakkı. Ücretsiz: her zaman; Premium/Aile: yalnız son %20'de. */
export function DocQuotaNote() {
  const { data: d } = useDocQuota();
  const plan = useMeta().data?.plan;
  const goPlan = useGoPlan();
  if (!d || d.unit !== "docs" || d.used == null || d.quota == null) return null;
  const left = Math.max(0, d.quota - d.used);
  const full = left === 0;
  const free = plan?.plan === "free";
  const family = plan?.plan === "aile";
  if (!free && left > d.quota * 0.2) return null;
  return (
    <section className="card flex flex-wrap items-center justify-between gap-2 p-4 text-sm" aria-live="polite">
      <p className={full ? "text-amber-800 dark:text-amber-300" : undefined}>
        {full
          ? <>Bu ayki <b>{d.quota}</b> belge okuma hakkın doldu; ay başında yenilenir ({renewDate()}).</>
          : <>Bu ay <b>{left}</b> belge okuma hakkın kaldı <span className="text-stone-500">({d.used}/{d.quota}{family ? ", ailece" : ""})</span>.</>}
      </p>
      {free && (
        <button className={full ? "btn-primary px-3 py-1.5 text-sm" : "btn-ghost border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700"}
          onClick={goPlan}>
          <Crown size={14} aria-hidden="true" /> {full ? "Premium'a geç" : `Premium: ayda ${PREMIUM_DOCS}`}
        </button>
      )}
    </section>
  );
}
