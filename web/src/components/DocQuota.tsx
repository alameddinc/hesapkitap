import { useQuery, useQueryClient } from "@tanstack/react-query";
import { bodyOf } from "../lib/apiError";
import { Crown, FileWarning } from "lucide-react";
import type { ReadCounter, ReadKind, ReadPack, ReadStatus } from "../api";
import { useUI } from "../ui";
import { useMeta } from "./hooks";
import Modal from "./Modal";
import { isDaily, KIND_LABEL, PackNote, ReadCounts } from "./Plan";

/** GET /api/me/ai. unit "docs": hazır sürüm, aylık okuma hakkı; belge ve fiş iki ayrı sayaç (kişi başı) + ek okuma
 *  paketi (extra, ikisinde ortak). used/quota: belge sayacı (eski gösterim). "ai": kendi sunucusu, yapay zekâ kotası.
 *  Yönetici: used/quota null, sayaç alanları yok. */
export interface AiUsage {
  used: number | null;
  quota: number | null;
  own_key: boolean;
  unit?: "docs" | "ai";
  shared_available?: boolean;
  belge?: ReadCounter;
  fis?: ReadCounter;
  extra?: number;
  /** günlük tek sayaç (sunucu "daily_reads") */
  reads?: ReadStatus;
}

/** Hak doldu (sunucu reddi): 502/422/402 gövdesi ya da yükleme sonucu. receipt_file: fiş fotoğrafı okunmadan saklandı.
 *  kind: dolan sayaç (eski sunucu göndermez: belge sayılır). */
export interface QuotaFull {
  manual: true;
  quota_full: true;
  limit?: number;
  upgrade?: boolean;
  detail?: string;
  message?: string;
  receipt_file?: string | null;
  reason?: "quota";
  kind?: ReadKind;
  trial?: boolean;
  trial_days_left?: number | null;
  packs?: ReadPack[];
  upgrade_yearly?: boolean;
  extra_left?: number;
  plan?: string;
  // günlük tek sayaç
  daily_limit?: number;
  daily_used?: number;
  resets_at?: string;
  needed?: number;
  left?: number;
  limited_by?: "day" | "month";
  monthly_cap?: number | null;
  reread?: boolean;
}

export const isQuotaFull = (j: unknown): j is QuotaFull =>
  !!j && typeof j === "object" && (j as { quota_full?: unknown }).quota_full === true;

/** fetch dışı akışlar (throw) için: hata nesnesi gövdeyi taşır. */
export class QuotaFullError extends Error {
  info: QuotaFull;
  constructor(info: QuotaFull) {
    super(info.detail ?? info.message ?? `Bu ayki ${KIND_LABEL[info.kind ?? "belge"]} okuma hakkın doldu`);
    this.info = info;
  }
}

/** Bir sonraki ayın 1'i: "1 Kasım". */
export function renewDate(today = new Date()): string {
  return new Date(today.getFullYear(), today.getMonth() + 1, 1).toLocaleDateString("tr-TR", { day: "numeric", month: "long" });
}

/** /api/me/ai → sayaçlar (yalnız hazır sürüm, yönetici değil). Yeni sunucuda reads günlük tek sayaç. */
export const readsOf = (d: AiUsage | null | undefined): ReadStatus | null =>
  d?.unit === "docs" && d.belge && d.fis ? { ...(d.reads ?? {}), belge: d.belge, fis: d.fis, extra: d.extra ?? 0 } : null;

/** Hak dolu başlığı ve ne zaman yenilendiği. */
export function fullTexts(info: Partial<QuotaFull> | null | undefined, reads: ReadStatus | null): { title: string; when: string } {
  const daily = isDaily(reads) || info?.daily_limit != null;
  if (!daily) {
    const noun = KIND_LABEL[info?.kind ?? "belge"];
    return { title: `Bu ayki ${noun} okuma hakkın doldu`, when: `Ay başında yenilenir (${renewDate()}).` };
  }
  const month = (info?.limited_by ?? reads?.limited_by) === "month";
  const monthWhen = `Ay başında yenilenir (${renewDate()}).`;
  if (info?.reread) {
    return { title: `Yeniden okuma ${info.needed ?? 2} hak ister`,
      when: `${month ? "Bu ay" : "Bugün"} ${info.left ?? 0} hakkın kaldı. ${month ? monthWhen : "Yarın yenilenir."}` };
  }
  if (month) return { title: `Bu ayki ${info?.monthly_cap ?? reads?.monthly_cap ?? ""} okuma hakkın doldu`.replace("  ", " "), when: monthWhen };
  const lim = info?.daily_limit ?? reads?.limit;
  const used = info?.daily_used ?? reads?.used;
  if ((info?.needed ?? 1) > 1 && (info?.left ?? 0) > 0) {
    return { title: `Bu belge ${info?.needed} okuma hakkı ister`, when: `Bugün ${info?.left} hakkın kaldı. Yarın yenilenir.` };
  }
  return { title: `Bugünkü okuma hakkın doldu${lim != null && used != null ? ` (${used}/${lim})` : ""}`, when: "Yarın yenilenir." };
}

/** Bu tür için hak kalmadı mı: hak (günlük ya da eski aylık) bitti ve ek paket yok. Günlükte iki tür aynı sayaç. */
export const kindFull = (r: ReadStatus, kind: ReadKind) => r[kind].left <= 0 && r.extra <= 0;

export function useDocQuota() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me-ai"], staleTime: 30_000,
    queryFn: () => fetch("/api/me/ai").then(async (r) => (r.ok ? await bodyOf(r) : null) as AiUsage | null),
  });
  const d = q.data;
  const reads = readsOf(d);
  // eski sunucu (tek sayaç): belge sayacı
  const legacyFull = !reads && d?.unit === "docs" && d.used != null && d.quota != null && d.used >= d.quota;
  /** hazır sürüm ve bu türde hak bitti: dosya seçici açılmaz */
  const fullFor = (kind: ReadKind) => (reads ? kindFull(reads, kind) : legacyFull);
  return {
    data: d,
    reads,
    fullFor,
    /** belge sayacı (ekstre, döküm) */
    full: fullFor("belge"),
    /** türü okunmadan bilinmeyen dosya (fiş mi fatura mı): iki sayaç da bitti */
    allFull: reads ? kindFull(reads, "belge") && kindFull(reads, "fis") : legacyFull,
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

/** Yükleme ekranları: "Okuma: Bugün 2/30 · Ek paket 0" (eski sunucu: "Bu ay: Belge 3/200 · Fiş 12/300 · Ek 0"). */
export function ReadsLine({ className }: { className?: string }) {
  const { reads } = useDocQuota();
  if (!reads) return null;
  return (
    <p className={className ?? "text-xs text-stone-500"} aria-live="polite">
      <span className="mr-1">{isDaily(reads) ? "Okuma:" : "Bu ay:"}</span><ReadCounts reads={reads} />
    </p>
  );
}

/** "Bu ayki belge/fiş okuma hakkın doldu" uyarısı. manual: birincil düğmeler (Elle ekle / Elle gider + Elle gelir).
 *  info: sunucunun ret gövdesi (kind, paketler, deneme); ön kontrolde yalnız kind. */
export function QuotaFullModal({ onClose, manual, upgrade, hasPhoto, info }: {
  onClose: () => void;
  manual: { label: string; onClick: () => void }[];
  upgrade?: boolean; // ücretsiz pakette: Premium bağlantısı
  hasPhoto?: boolean; // fiş fotoğrafı saklandı, forma ekli gider
  info?: Partial<QuotaFull> | null;
}) {
  const plan = useMeta().data?.plan;
  const { reads } = useDocQuota();
  const goPlan = useGoPlan();
  const kind: ReadKind = info?.kind ?? "belge";
  const noun = KIND_LABEL[kind];
  const showUpgrade = upgrade ?? info?.upgrade ?? plan?.plan === "free";
  const premium = plan?.reads_by_plan?.premium?.[kind];
  const premDaily = plan?.daily_reads_by_plan?.premium.day;
  const t = fullTexts(info, reads);
  const trial = info?.trial ?? plan?.trial;
  const daysLeft = info?.trial_days_left ?? plan?.trial_days_left;
  return (
    <Modal title={t.title} onClose={onClose} footer={<>
      {manual.map((m, i) => (
        <button key={m.label} className={i === 0 ? "btn-primary flex-1 justify-center" : "btn-ghost flex-1 justify-center border border-stone-300 dark:border-stone-700"}
          onClick={m.onClick} autoFocus={i === 0}>{m.label}</button>
      ))}
    </>}>
      <div className="flex items-start gap-3 text-sm">
        <FileWarning size={22} className="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
        <div className="min-w-0 space-y-1.5">
          <p>{t.when} Bu arada harcamanı elle ekleyebilirsin; hiçbir şey kilitlenmez, yalnız okuma durur.</p>
          {reads && <p className="text-stone-600 dark:text-stone-400"><ReadCounts reads={reads} /></p>}
          {hasPhoto && <p className="text-xs text-stone-500">Fotoğraf okunmadan saklandı; elle eklediğin gidere ekli gelir.</p>}
          {trial && <p className="text-xs text-stone-500">Deneme süresindesin{daysLeft != null ? ` (${daysLeft} gün kaldı)` : ""}; haklar paketin normal haklarıdır.</p>}
          <PackNote p={plan} />
          {info?.upgrade_yearly && <p className="text-xs text-stone-500">Yıllık aboneliğe telefondaki uygulamadan geçebilirsin.</p>}
          {showUpgrade && (
            <button className="btn-ghost mt-1 border border-amber-300 px-3 py-1.5 text-sm text-amber-800 dark:border-amber-800 dark:text-amber-300"
              onClick={() => { onClose(); goPlan(); }}>
              <Crown size={14} aria-hidden="true" /> {premDaily ? `Premium: günde ${premDaily} okuma` : premium ? `Premium: ayda ${premium} ${noun}` : "Paketleri gör"}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Ayarlar: hazır sürümde kalan okuma hakları. Ücretsiz: her zaman; Premium/Aile: bir sayaç son %20'deyse. */
export function DocQuotaNote() {
  const { data: d, reads } = useDocQuota();
  const plan = useMeta().data?.plan;
  const goPlan = useGoPlan();
  const free = plan?.plan === "free";
  const prem = plan?.reads_by_plan?.premium;
  const premDaily = plan?.daily_reads_by_plan?.premium.day;
  if (isDaily(reads)) {
    const out = reads.left <= 0 && reads.extra <= 0;
    const low = reads.left + reads.extra <= reads.limit * 0.2;
    if (!free && !low) return null;
    const t = fullTexts(null, reads);
    return (
      <section className="card space-y-2 p-4 text-sm" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0"><span className="mr-1.5 text-stone-500">Okuma hakkın:</span><ReadCounts reads={reads} /></p>
          {free && (
            <button className={out ? "btn-primary px-3 py-1.5 text-sm" : "btn-ghost border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700"}
              onClick={goPlan}>
              <Crown size={14} aria-hidden="true" /> {out || !premDaily ? "Paketleri gör" : `Premium: günde ${premDaily} okuma`}
            </button>
          )}
        </div>
        {out && <>
          <p className="text-amber-800 dark:text-amber-300">{t.title}. {t.when} Elle ekleme açık.</p>
          <PackNote p={plan} />
        </>}
      </section>
    );
  }
  if (reads) {
    const kinds: ReadKind[] = ["belge", "fis"];
    const out = kinds.filter((k) => kindFull(reads, k));
    const low = kinds.some((k) => reads[k].left + reads.extra <= reads[k].limit * 0.2);
    if (!free && !low) return null;
    return (
      <section className="card space-y-2 p-4 text-sm" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0"><span className="mr-1.5 text-stone-500">Bu ayki okuma hakların:</span><ReadCounts reads={reads} /></p>
          {free && (
            <button className={out.length ? "btn-primary px-3 py-1.5 text-sm" : "btn-ghost border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700"}
              onClick={goPlan}>
              <Crown size={14} aria-hidden="true" /> {out.length ? "Paketleri gör" : prem ? `Premium: ${prem.belge} belge, ${prem.fis} fiş` : "Paketleri gör"}
            </button>
          )}
        </div>
        {out.length > 0 && <>
          <p className="text-amber-800 dark:text-amber-300">
            {out.map((k) => KIND_LABEL[k]).join(" ve ")} okuma hakkın doldu; ay başında yenilenir ({renewDate()}). Elle ekleme açık.
          </p>
          <PackNote p={plan} />
        </>}
      </section>
    );
  }
  // eski sunucu: tek sayaç (belge)
  if (!d || d.unit !== "docs" || d.used == null || d.quota == null) return null;
  const left = Math.max(0, d.quota - d.used);
  const full = left === 0;
  if (!free && left > d.quota * 0.2) return null;
  return (
    <section className="card flex flex-wrap items-center justify-between gap-2 p-4 text-sm" aria-live="polite">
      <p className={full ? "text-amber-800 dark:text-amber-300" : undefined}>
        {full
          ? <>Bu ayki <b>{d.quota}</b> belge okuma hakkın doldu; ay başında yenilenir ({renewDate()}).</>
          : <>Bu ay <b>{left}</b> belge okuma hakkın kaldı <span className="text-stone-500">({d.used}/{d.quota})</span>.</>}
      </p>
      {free && (
        <button className={full ? "btn-primary px-3 py-1.5 text-sm" : "btn-ghost border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700"}
          onClick={goPlan}>
          <Crown size={14} aria-hidden="true" /> {full ? "Premium'a geç" : "Paketleri gör"}
        </button>
      )}
    </section>
  );
}
