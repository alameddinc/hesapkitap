// Var olan hesapla ortak deftere katılma (yetenek book_join; mobil components/defter/DavetKatil.tsx ve
// screens/OrtakDefter.tsx ile aynı akış ve metinler). GET /api/book/join/{kod} önizleme → "Katıl" POST aynı adres;
// POST /api/book/leave üye ayrılır (Benim defteri kalır). Eski sunucuda uç yok: önce çıkış yap.
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronRight, Lock, LogOut, Star, Users2 } from "lucide-react";
import { useState } from "react";
import { call, send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { useUI } from "../ui";
import { type BookRole, type Seat, useCap, useSave, useSwitchBook } from "./hooks";
import Modal from "./Modal";
import { useGoPlan } from "./DocQuota";
import { possessive } from "./InvitePreview";

export interface JoinPreview {
  valid: boolean; can_join: boolean; reason?: string; message?: string;
  owner?: string; owner_email?: string | null; role?: BookRole; role_label?: string; expires_at?: string | null;
  /** kendi Premium'u var: Aile koltuğu kullanmaz */
  own_subscription?: boolean;
}
interface JoinResult { ok: true; owner: string; role: BookRole; role_label?: string; own_subscription: boolean }

const ROLE_LABEL: Record<string, string> = { viewer: "Sadece görür", editor: "Görür ve ekler", full: "Tam yetki" };
const short = (u?: string) => (u ?? "").split("@")[0];

const CODE_RE = /^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$/;
/** Yazılan kodu biçimle: büyük harf, boşluksuz, 4-4-4 tireli (mobil lib/davet.ts kodBicim). */
export function formatCode(v: string): string {
  const x = v.toUpperCase().replace(/[^0-9A-F]/g, "");
  if (x.length === 12) return `${x.slice(0, 4)}-${x.slice(4, 8)}-${x.slice(8)}`;
  return v.toUpperCase().replace(/\s+/g, "");
}
export const codeValid = (v: string) => CODE_RE.test(v);

/** Sunucunun reddetme nedenine göre ek yol: zaten başka defterde / kendi üyeleri var → Ayarlar (Ortak defter). */
const PATH: Record<string, string> = {
  other_book: "Ortak defter ayarlarına git", already_member: "Ortak defter ayarlarına git", has_members: "Üyelerimi gör",
};

const trDay = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("tr-TR", { day: "numeric", month: "long" }) : "");

/** Hesabı açık kullanıcı davet bağlantısını açtı (ya da kodu yazdı): "Ortak deftere davet" penceresi. */
export function JoinPreviewModal({ code, onClose }: { code: string; onClose: () => void }) {
  const ui = useUI();
  const on = useCap("book_join");
  const switchBook = useSwitchBook();
  const pv = useQuery({ queryKey: ["book-join", code], queryFn: () => send<JoinPreview>(`/api/book/join/${encodeURIComponent(code)}`),
    enabled: on, retry: false, staleTime: 0 });
  const join = useSave(() => call<JoinResult>("POST", `/api/book/join/${encodeURIComponent(code)}`), (r) => {
    onClose();
    switchBook("ortak"); // bütün veriler artık ortak defterden (Benim: kendi eski verin)
    ui.toast(r.own_subscription
      ? `${possessive(short(r.owner))} ortak defterine katıldın · Premium'un sürdüğü için Aile koltuğu kullanmıyorsun`
      : `${possessive(short(r.owner))} ortak defterine katıldın`);
  }, { inline: true });
  const d = pv.data;
  const reason = d && !d.can_join ? d.reason ?? "" : "";
  const owner = short(d?.owner);

  return (
    <Modal title="Ortak deftere davet" onClose={onClose}>
      <div className="space-y-3 text-sm">
        {!on ? (
          <>
            <p className="text-stone-600 dark:text-stone-300">Bu sunucu, hesabın açıkken davetle katılmayı henüz desteklemiyor. Davetle
              katılmak için önce çıkış yap; kod “Hesap aç” ekranında dolu gelir.</p>
            <button className="btn-primary w-full justify-center py-2" onClick={onClose}>Tamam</button>
          </>
        ) : pv.isLoading ? (
          <p className="text-stone-500" aria-busy="true">Davet kontrol ediliyor…</p>
        ) : pv.error ? (
          <>
            <Banner kind="warn">{(pv.error as Error).message}</Banner>
            <div className="flex gap-2">
              <button className="btn-primary flex-1 justify-center py-2" onClick={() => void pv.refetch()}>Tekrar dene</button>
              <button className="btn-ghost flex-1 justify-center py-2" onClick={onClose}>Vazgeç</button>
            </div>
          </>
        ) : d && d.valid && d.can_join ? (
          <>
            <div className="flex items-center gap-3">
              <span className="flex size-12 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300">
                <Users2 size={22} aria-hidden /></span>
              <span className="min-w-0 flex-1 text-base break-words"><b>{owner}</b> seni ortak defterine davet ediyor</span>
            </div>
            <dl className="divide-y divide-stone-100 rounded-xl border border-stone-200 dark:divide-stone-800 dark:border-stone-700">
              <div className="flex items-center justify-between gap-3 px-3 py-2.5">
                <dt>Rol</dt><dd className="text-right font-semibold text-stone-600 dark:text-stone-300">{d.role_label ?? ROLE_LABEL[d.role ?? "editor"]}</dd>
              </div>
              {d.owner_email && (
                <div className="flex items-center justify-between gap-3 px-3 py-2.5">
                  <dt>Davet eden</dt><dd className="min-w-0 text-right break-all text-stone-600 dark:text-stone-300">{d.owner_email}</dd>
                </div>
              )}
            </dl>
            <Banner kind="info" icon={Lock}>Kendi verilerin “Benim” defterinde kalır, kimse göremez. Ortak defter ile Benim arasında
              istediğin an geçersin.</Banner>
            {d.own_subscription && <Banner kind="info" icon={Star}>Premium'un devam ettiği için Aile koltuğu kullanmıyorsun.</Banner>}
            {join.error && <Banner kind="warn">{join.error.message}</Banner>}
            <div className="flex gap-2">
              <button className="btn-primary flex-1 justify-center py-2" disabled={join.isPending} onClick={() => join.mutate(undefined)}>
                {join.isPending ? "Katılınıyor…" : "Katıl"}</button>
              <button className="btn-ghost flex-1 justify-center py-2" onClick={onClose}>Vazgeç</button>
            </div>
          </>
        ) : d ? (
          <>
            {owner && <p className="text-stone-600 dark:text-stone-300"><b className="text-stone-900 dark:text-stone-100">{owner}</b> seni ortak defterine davet ediyor.</p>}
            <Banner kind="warn">{d.message ?? "Bu davetle katılamazsın."}</Banner>
            <div className="flex gap-2">
              {PATH[reason] && <button className="btn-primary flex-1 justify-center py-2"
                onClick={() => { onClose(); ui.go("ayarlar"); }}>{PATH[reason]}</button>}
              <button className="btn-ghost flex-1 justify-center py-2" onClick={onClose}>Tamam</button>
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
}

function Banner({ kind, icon: Icon = AlertTriangle, children }: { kind: "warn" | "info"; icon?: typeof AlertTriangle; children: React.ReactNode }) {
  return (
    <div role={kind === "warn" ? "alert" : "note"} className={kind === "warn"
      ? "flex gap-2 rounded-lg bg-amber-50 p-2.5 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
      : "flex gap-2 rounded-lg bg-blue-50 p-2.5 text-blue-900 dark:bg-blue-950/40 dark:text-blue-200"}>
      <Icon size={16} className="mt-0.5 shrink-0" aria-hidden /><span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}

/** Başkasının ortak defterine katıl: kodu yaz → "Ortak deftere davet" penceresi. */
export function JoinWithCode() {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  if (!useCap("book_join")) return null;
  const ok = codeValid(code);
  return (
    <section className="card space-y-2 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Users2 size={18} /> Başka bir deftere katıl</h2>
      <button className="flex w-full items-center gap-2 rounded-lg border border-stone-200 px-3 py-2.5 text-left text-sm hover:bg-stone-50 dark:border-stone-700 dark:hover:bg-stone-800/50"
        onClick={() => setOpen(true)}>
        <span className="flex-1 font-medium">Davet kodum var</span><ChevronRight size={16} className="text-stone-400" aria-hidden />
      </button>
      <p className="text-xs text-stone-500">Biri seni kendi ortak defterine çağırdıysa. Kendi verin “Benim” defterinde kalır.</p>
      {open && (
        <Modal title="Davet kodum var" onClose={() => setOpen(false)}>
          <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); if (ok) { setOpen(false); setPreview(code); } }}>
            <label className="block"><span className="mb-1 block text-xs text-stone-500">Kod</span>
              <input className="input font-mono tracking-wide" aria-label="Davet kodu" autoCapitalize="characters" autoComplete="off" spellCheck={false}
                placeholder="AB12-CD34-EF56" value={code} onChange={(e) => setCode(formatCode(e.target.value))} autoFocus /></label>
            <p className="text-xs text-stone-500">Davet bağlantın varsa tarayıcıda açman yeter: davet kendiliğinden gelir.</p>
            <div className="flex gap-2">
              <button type="submit" className="btn-primary flex-1 justify-center py-2" disabled={!ok}>Devam</button>
              <button type="button" className="btn-ghost flex-1 justify-center py-2" onClick={() => setOpen(false)}>Vazgeç</button>
            </div>
          </form>
        </Modal>
      )}
      {preview && <JoinPreviewModal code={preview} onClose={() => setPreview(null)} />}
    </section>
  );
}

/** Üye: ortak defterden ayrıl (onaylı). Eski sunucuda: sahibinden çıkarmasını iste. */
export function LeaveBook() {
  const ui = useUI();
  const on = useCap("book_join");
  const switchBook = useSwitchBook();
  const leave = useSave(() => call("POST", "/api/book/leave"), () => {
    switchBook("ortak"); // artık tek defter: Benim (hesabının kendi defteri)
    ui.toast("Ortak defterden ayrıldın; kendi defterin açık");
  });
  if (!on) return <p className="text-xs text-stone-500">Ayrılmak için defter sahibinden seni çıkarmasını iste.</p>;
  return (
    <button className="btn-danger w-full justify-center py-2" disabled={leave.isPending}
      onClick={() => void ask("Ortak defterden ayrılsın mı?\n\nOrtak defteri artık göremezsin; Benim defterin kalır. Yeniden katılmak için yeni davet gerekir.",
        { confirm: "Ayrıl", danger: true, irreversible: false }).then((ok) => { if (ok) leave.mutate(undefined); })}>
      <LogOut size={16} /> {leave.isPending ? "Ayrılınıyor…" : "Ortak defterden ayrıl"}
    </button>
  );
}

/** Üyenin Aile koltuğu: kendi Premium'u (bilgi) / koltuk yok (ek süre ya da ücretsiz sınırlar). Koltuktaysa bir şey yazmaz. */
export function SeatNote({ seat, plans }: { seat?: Seat | null; /** sayfada paket bölümü var: "Paketleri gör" */ plans?: boolean }) {
  const goPlan = useGoPlan();
  if (!seat || seat.state === "seat") return null;
  if (seat.state === "own") return <Banner kind="info" icon={Star}>Premium'un devam ettiği için Aile koltuğu kullanmıyorsun.</Banner>;
  return (
    <Banner kind="warn">
      <b>Premium'un bitti, Aile koltukları dolu.</b>{" "}
      {seat.state === "grace" ? `Premium ${trDay(seat.grace_until)} tarihine kadar açık.` : "Premium özellikleri kapandı; ortak defteri görmeye devam edersin."}
      {" "}Premium'u yenileyebilir ya da aile yöneticisinden koltuk isteyebilirsin.
      {plans && <button className="mt-1 block font-semibold text-blue-700 underline-offset-2 hover:underline dark:text-blue-300" onClick={goPlan}>Paketleri gör</button>}
    </Banner>
  );
}

/** Aile koltuğu (sahip görünümü, üye satırı): kısa etiket */
export const seatLabel = (s?: Seat | null) =>
  !s ? null : s.state === "own" ? "kendi Premium'u" : s.state === "grace" ? `koltuk bekliyor · Premium ek süresi ${trDay(s.grace_until)}`
    : s.state === "none" ? "koltuk bekliyor" : null;

/** Sahip görünümü: üyenin koltuk durumu (uzun metin) */
export const seatText = (s: Seat) => s.state === "own" ? "Kendi Premium'u var; Aile koltuğu kullanmıyor."
  : s.state === "seat" ? "Aile koltuğunda; Premium özellikleri açık."
    : s.state === "grace" ? `Premium'u bitti, koltuklar dolu. Ek süre ${trDay(s.grace_until)} tarihinde biter; sonra ücretsiz paket sınırlarına düşer.`
      : "Premium'u bitti, koltuklar dolu: ücretsiz paket sınırlarında. Ortak defteri görmeye devam eder.";
