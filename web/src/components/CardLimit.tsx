import { hataMetni } from "../lib/hataAcikla";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { call } from "../lib/apiError";
import type { CardInterest } from "../api";
import type { EarlyCloseInfo } from "./EarlyClose";
import { cx, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { isLimited, useBook, useMeta, useSave } from "./hooks";
import Modal from "./Modal";

/** Kart limiti alanları (sunucu finance.card_limit; yetenek "card_limit"). available eksi olabilir (limit aşıldı). */
export interface CardLimitInfo {
  card_no: string; limit: number | null; limit_source: "ekstre" | "elle" | null; statement_limit: number | null;
  available: number | null; available_source: "ekstre" | "tahmini" | null; available_as_of: string | null;
  used: number | null; used_pct: number | null;
  /** son ekstrenin faiz satırı (yetenek card_interest; eski sunucuda yok) */
  interest?: CardInterest | null;
  /** taksit erken kapama (yetenek card_early_close; eski sunucuda yok) */
  early_close?: EarlyCloseInfo | null;
}
interface Summary { cards: CardLimitInfo[] }

/** Kart başına limit (GET /api/cards/summary); sunucu eskiyse (yetenek yok) hiç istenmez ve hiçbir şey gösterilmez. */
export function useCardLimits(wanted = true) {
  const meta = useMeta();
  const on = wanted && !!meta.data?.capabilities?.includes("card_limit");
  return useQuery({
    queryKey: ["cards-summary", 0], enabled: on,
    queryFn: () => call<Summary>("GET", "/api/cards/summary?recent=0"),
    select: (d) => new Map(d.cards.filter((c) => c.limit !== undefined).map((c) => [c.card_no, c])),
  });
}

/** Hesaplar'daki kredi kartı satırının altında: "Kalan limit ₺X / ₺Y", ince doluluk çubuğu, düzenle / Limit ekle. */
export default function CardLimit({ info }: { info?: CardLimitInfo }) {
  const [edit, setEdit] = useState(false);
  const canEdit = !isLimited(useBook().data); // sahip ya da tam yetkili üye (sunucu da ayrıca uygular)
  if (!info) return null;
  if (info.limit == null) {
    return (
      <>
        {canEdit && <button className="mt-0.5 block text-xs font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setEdit(true)}>Limit ekle</button>}
        {edit && <CardLimitForm info={info} onClose={() => setEdit(false)} />}
      </>
    );
  }
  const pct = Math.min(100, Math.max(0, info.used_pct ?? 0));
  const over = (info.available ?? 0) < 0;
  return (
    <span className="mt-1 block">
      <span className="flex flex-wrap items-baseline gap-x-2 text-xs text-stone-500">
        <span>Kalan limit <b className={cx("tabular-nums", over ? "text-red-600" : "text-stone-700 dark:text-stone-200")}>{tl(info.available ?? 0)}</b> / <span className="tabular-nums">{tl(info.limit)}</span></span>
        <span>{info.available_source === "ekstre" ? "ekstredeki" : "tahmini"}{info.limit_source === "elle" ? " · limiti sen girdin" : ""}</span>
        {canEdit && <button className="font-medium text-blue-700 hover:underline dark:text-blue-400" onClick={() => setEdit(true)}>Düzenle</button>}
      </span>
      <span role="img" aria-label={`Limitin yüzde ${Math.round(pct)}'i kullanılıyor`}
        className="mt-1 block h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
        <span className={cx("block h-full rounded-full", pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-blue-600")} style={{ width: `${pct}%` }} />
      </span>
      {edit && <CardLimitForm info={info} onClose={() => setEdit(false)} />}
    </span>
  );
}

/** Limiti elle gir / düzelt (PUT /api/cards/limit); elle girilmişse ekstredeki limite dönülebilir. */
function CardLimitForm({ info, onClose }: { info: CardLimitInfo; onClose: () => void }) {
  const ui = useUI();
  const [v, setV] = useState(info.limit != null ? String(info.limit).replace(".", ",") : "");
  const [err, setErr] = useState<string | null>(null);
  const save = useSave((limit: number | null) => call<CardLimitInfo>("PUT", "/api/cards/limit", { card_no: info.card_no, limit }),
    (_r, limit) => { ui.toast(limit == null ? (info.statement_limit ? "Ekstredeki limite dönüldü" : "Limit kaldırıldı") : "Limit kaydedildi"); onClose(); });
  const submit = () => {
    const n = parseAmount(v);
    if (!(n > 0)) { setErr("Limiti yaz"); return; }
    setErr(null);
    save.mutate(n, { onError: (e) => setErr(hataMetni(e)) });
  };
  return (
    <Modal title="Kart limiti" onClose={onClose} footer={
      <>
        {info.limit_source === "elle" && (
          <button className="btn-ghost" disabled={save.isPending} onClick={() => save.mutate(null, { onError: (e) => setErr(hataMetni(e)) })}>
            {info.statement_limit ? "Ekstredeki limite dön" : "Limiti kaldır"}</button>
        )}
        <div className="flex-1" />
        <button className="btn-ghost" onClick={onClose}>Vazgeç</button>
        <button className="btn-primary" disabled={save.isPending} onClick={submit}>Kaydet</button>
      </>
    }>
      <div className="space-y-2">
        <label className="label" htmlFor="c-limit">Limit (₺)</label>
        <input id="c-limit" className="input text-right" inputMode="decimal" value={v} autoFocus placeholder="0,00"
          onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        <p className="text-xs text-stone-500">
          {info.statement_limit ? `Ekstrede limit ${tl(info.statement_limit)} yazıyor. ` : "Ekstreden limit okunamadı. "}
          Limit arttıysa ya da ekstrede yoksa buradan gir; kalan limit buna göre hesaplanır.
        </p>
        {err && <p className="text-sm text-red-600 dark:text-red-400" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}
