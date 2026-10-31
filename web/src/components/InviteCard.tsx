import { AlertTriangle, Copy, RefreshCw, Share2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { cx, dateTR } from "../format";

export interface Invite {
  code: string; note: string | null; expires_at: string | null; book_role?: string | null;
  /** sunucu 81844c5: web bağlantısı (QR'a bu yazılır) ve uygulama derin bağlantısı */
  link?: string; app_link?: string; kind?: "book" | "account"; role_label?: string;
}

/** İstemcide QR (qrcode-generator, gerektiğinde yüklenir): koyu modüller tek SVG yolu; zemin her temada beyaz
 *  (telefon kamerası koyu zeminli ters QR'ı okumayabilir). */
export function QR({ text, size = 176, label }: { text: string; size?: number; label: string }) {
  const [svg, setSvg] = useState<{ n: number; d: string } | null>(null);
  useEffect(() => {
    let live = true;
    void import("qrcode-generator").then(({ default: qrcode }) => {
      const qr = qrcode(0, "M");
      qr.addData(text);
      qr.make();
      const n = qr.getModuleCount();
      let d = "";
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + 4},${r + 4}h1v1h-1z`;
      if (live) setSvg({ n: n + 8, d });
    });
    return () => { live = false; };
  }, [text]);
  return (
    <div className="inline-flex rounded-lg bg-white p-1 shadow-sm ring-1 ring-stone-200 dark:ring-stone-700" style={{ width: size, height: size }}>
      {svg ? (
        <svg viewBox={`0 0 ${svg.n} ${svg.n}`} width="100%" height="100%" role="img" aria-label={label} shapeRendering="crispEdges">
          <path d={svg.d} fill="#000" />
        </svg>
      ) : <span className="m-auto text-xs text-stone-400">QR hazırlanıyor…</span>}
    </div>
  );
}

/** Ortak defter daveti: QR, bağlantı, kopyala / paylaş, yeni kod üret, iptal; süre ve yetki; paylaşım uyarısı. */
export default function InviteCard({ inv, fallbackLink, roleLabel, onCopy, onRenew, onCancel, busy, open, onToggle }: {
  inv: Invite; fallbackLink: string; roleLabel?: string; busy?: boolean; open: boolean; onToggle: () => void;
  onCopy: (t: string) => void; onRenew: () => void; onCancel: () => void;
}) {
  const link = inv.link || fallbackLink;
  const [today] = useState(() => new Date().toISOString().slice(0, 10)); // render saf kalsın: bugün bir kez okunur
  const expired = !!inv.expires_at && inv.expires_at < today;
  const canShare = typeof navigator !== "undefined" && "share" in navigator;
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="min-w-0 flex-1 text-left" aria-expanded={open} onClick={onToggle}>
          <span className="block font-medium break-words text-stone-800 dark:text-stone-100">Davet{inv.note ? ` · ${inv.note}` : ""}</span>
          <span className={cx("block text-xs", expired ? "text-red-700 dark:text-red-400" : "text-stone-500 dark:text-stone-400")}>
            {[inv.role_label ?? roleLabel, inv.expires_at ? (expired ? `süresi doldu (${dateTR(inv.expires_at)})` : `${dateTR(inv.expires_at)}'e kadar geçerli`) : null, "tek kullanımlık"]
              .filter(Boolean).join(" · ")}</span>
        </button>
        <button className="btn-ghost px-2 py-1 text-xs" onClick={onToggle}>{open ? "Gizle" : "QR ve bağlantı"}</button>
      </div>
      {open && (
        <div className="mt-2 space-y-3 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
          <div className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
            <QR text={link} label={`Davet QR kodu${inv.note ? `: ${inv.note}` : ""}`} />
            <div className="min-w-0 flex-1 space-y-2 text-sm">
              <p className="text-stone-600 dark:text-stone-300">Davet ettiğin kişi telefonunun kamerasıyla okutsun ya da bağlantıyı açsın;
                hesabını açınca bu deftere <b>{inv.role_label ?? roleLabel ?? "üye"}</b> olarak katılır.</p>
              <code className="block rounded bg-stone-100 p-2 text-xs break-all dark:bg-stone-800">{link}</code>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary px-3 py-1 text-xs" onClick={() => onCopy(link)}><Copy size={14} aria-hidden /> Kopyala</button>
                {canShare && (
                  <button className="btn border border-stone-300 px-3 py-1 text-xs dark:border-stone-700"
                    onClick={() => void navigator.share({ title: "HesapKitap daveti", text: "Ortak defterime katıl:", url: link }).catch(() => undefined)}>
                    <Share2 size={14} aria-hidden /> Paylaş</button>
                )}
                <button className="btn border border-stone-300 px-3 py-1 text-xs dark:border-stone-700" disabled={busy} onClick={onRenew}>
                  <RefreshCw size={14} aria-hidden /> Yeni kod üret</button>
                <button className="btn-danger px-3 py-1 text-xs" disabled={busy} onClick={onCancel}><Trash2 size={14} aria-hidden /> İptal et</button>
              </div>
            </div>
          </div>
          <p className="flex gap-1.5 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <AlertTriangle size={14} className="mt-px shrink-0" aria-hidden />
            Bağlantıyı yalnız davet ettiğin kişiyle paylaş: bağlantıyı ilk kullanan deftere girer. Yanlış kişiye gittiyse “Yeni kod üret” de; eski bağlantı çalışmaz.
          </p>
        </div>
      )}
    </li>
  );
}
