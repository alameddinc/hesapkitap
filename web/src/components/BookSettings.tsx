import { useQuery } from "@tanstack/react-query";
import { ask } from "../lib/ask";
import { call as apiCall } from "../lib/apiError";
import { ChevronDown, Copy, EyeOff, KeyRound, Trash2, Users2 } from "lucide-react";
import { useState } from "react";
import { cx, maskText, tsDateTR } from "../format";
import { useUI } from "../ui";
import { type BookRole, useLoginKind, useSave } from "./hooks";
import { needLabel, usePlan } from "./Plan";
import InviteCard, { type Invite } from "./InviteCard";
import { JoinWithCode, seatLabel, seatText } from "./JoinBook";
import Modal from "./Modal";
import type { Seat } from "./hooks";
import SorguDurumu, { KartIskelet } from "./SorguDurumu";

interface Rules { hidden_cards: string[]; hidden_accounts: number[]; hide_fees: boolean; hidden_pages: string[] }
interface Member { id: number; username: string; last_login: string | null; disabled: boolean; role: BookRole; own_rules: boolean; rules: Rules;
  joined?: boolean /* var olan hesabıyla katıldı: şifresini sahip sıfırlayamaz */;
  seat?: Seat | null /* book_join, hazır sürüm: Aile koltuğu */ }
interface Seats { total: number; used: number; members: number; max_members: number; waiting: number }
interface Book {
  members: Member[];
  invites: Invite[];
  rules: Rules; cards: { card_no: string; bank: string }[]; accounts: { id: number; name: string; kind: string }[];
  pages: Record<string, string>; roles?: Record<string, string>;
  /** book_join, hazır sürüm: "3 koltuktan 2'si dolu"; davet en çok max_members üyeye kadar */
  seats?: Seats;
}

/** Türkçe iyelik eki (sayıdan sonra): 1'i 2'si 3'ü … */
const suffix = (n: number) => {
  const last = n % 10, tens = n % 100;
  const map: Record<number, string> = { 0: "'ı", 1: "'i", 2: "'si", 3: "'ü", 4: "'ü", 5: "'i", 6: "'sı", 7: "'si", 8: "'i", 9: "'u" };
  if (last === 0 && tens !== 0) return ({ 10: "'u", 20: "'si", 30: "'u", 40: "'ı", 50: "'si", 60: "'ı", 70: "'i", 80: "'i", 90: "'ı" } as Record<number, string>)[tens] ?? "'ı";
  return map[last];
};
const short = (u: string) => u.split("@")[0];

const ROLES: { value: BookRole; label: string; sub: string }[] = [
  { value: "viewer", label: "Sadece görür", sub: "Her şeyi görür, bir şey ekleyemez." },
  { value: "editor", label: "Görür ve ekler", sub: "Fiş ve harcama ekler, kategori düzeltir; silemez." },
  { value: "full", label: "Tam yetki", sub: "Ekstre yükler, düzenler, siler. Ayarlar ve üyeler sende kalır." },
];

const call = <T,>(url: string, method = "GET", body?: unknown) => apiCall<T>(method, url, body);
const link = (code: string) => `${window.location.origin}/#/kayit?kod=${code}`;
const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
const hiddenCount = (r: Rules) => r.hidden_cards.length + r.hidden_accounts.length + r.hidden_pages.length + (r.hide_fees ? 1 : 0);

/** Gizleme kuralları düzenleyici: ortak kurallar ya da tek bir üyeye özel kurallar. */
function RulesEditor({ d, r, onChange }: { d: Book; r: Rules; onChange: (p: Partial<Rules>) => void }) {
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={r.hide_fees} onChange={(e) => onChange({ hide_fees: e.target.checked })} />
        Faiz, gecikme ve kart ücretleri (faiz alarmı dahil)
      </label>
      {d.cards.length > 0 && <div>
        <div className="text-xs text-stone-500">Kartlar: işlemleri, ekstreleri, tahminleri</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {d.cards.map((c) => (
            <label key={c.card_no} className="flex items-center gap-2">
              <input type="checkbox" checked={r.hidden_cards.includes(c.card_no)}
                onChange={() => onChange({ hidden_cards: toggle(r.hidden_cards, c.card_no) })} />
              {c.bank} ··{c.card_no.slice(-4)}
            </label>
          ))}
        </div>
      </div>}
      {d.accounts.length > 0 && <div>
        <div className="text-xs text-stone-500">Hesaplar: bakiyesi ve hareketleri</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {d.accounts.map((a) => (
            <label key={a.id} className="flex items-center gap-2">
              <input type="checkbox" checked={r.hidden_accounts.includes(a.id)}
                onChange={() => onChange({ hidden_accounts: toggle(r.hidden_accounts, a.id) })} />
              {a.name}
            </label>
          ))}
        </div>
      </div>}
      <div>
        <div className="text-xs text-stone-500">Sayfalar (Kurallar ve Ekstreler tam yetki dışında zaten kapalı)</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {Object.entries(d.pages).map(([k, label]) => (
            <label key={k} className="flex items-center gap-2">
              <input type="checkbox" checked={r.hidden_pages.includes(k)}
                onChange={() => onChange({ hidden_pages: toggle(r.hidden_pages, k) })} />
              {label}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Üye satırı: yetki seçici; dokununca "Bu kişiden neleri gizle". */
function MemberRow({ m, d }: { m: Member; d: Book }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const onErr = { onError: (e: Error) => ui.error(e) };
  const setRole = useSave((role: BookRole) => call(`/api/book/members/${m.id}`, "PATCH", { book_role: role }), () => ui.toast("Yetki değişti"));
  const setRules = useSave((r: Partial<Rules>) => call(`/api/book/rules?member=${m.id}`, "PUT", r));
  const toShared = useSave(() => call(`/api/book/members/${m.id}/rules`, "DELETE"), () => ui.toast("Ortak kurallar geçerli"));
  const remove = useSave(() => call(`/api/book/members/${m.id}`, "DELETE"), () => ui.toast("Üye çıkarıldı"));
  const reset = useSave(() => call<{ temp_password: string }>(`/api/book/members/${m.id}/reset-password`, "POST"),
    (r) => setShown(r.temp_password));
  const n = hiddenCount(m.rules);
  // Aile koltuğu: bekleyen üyeye koltuklu birinin koltuğunu ver
  const [pick, setPick] = useState(false);
  const seated = d.members.filter((x) => x.id !== m.id && x.seat?.state === "seat");
  const waiting = m.seat?.state === "grace" || m.seat?.state === "none";
  const give = useSave((from: number) => call(`/api/book/members/${m.id}/seat`, "POST", { from_member: from }),
    () => { setPick(false); ui.toast(`${short(m.username)} Aile koltuğuna geçti`); });
  const giveFrom = (from: Member) => {
    const f = short(from.username), name = short(m.username);
    void ask(`Koltuk el değiştirsin mi?\n\n${f} adlı üyenin koltuğu ${name} adlı üyeye geçer. ${f} ücretsiz paket sınırlarına düşer; ortak defteri görmeye devam eder. İkisine de bildirim gider.`,
      { confirm: "Koltuğu ver", danger: false }).then((ok) => { if (ok) give.mutate(from.id, onErr); });
  };
  const sl = seatLabel(m.seat);
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className="flex min-w-0 flex-1 items-center gap-1 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="min-w-0">
            <b className="break-all">{maskText(m.username)}</b>
            <span className="block text-xs text-stone-500">
              {m.last_login ? `son giriş ${tsDateTR(m.last_login)}` : "henüz girmedi"}
              {sl && <>{" · "}<span className={waiting ? "text-amber-700 dark:text-amber-400" : undefined}>{sl}</span></>}
              {" · "}{m.own_rules ? "kendine özel kural" : "ortak kurallar"}{n ? ` (${n} gizli)` : ""}
            </span>
          </span>
          <ChevronDown size={14} className={cx("shrink-0 text-stone-400 transition-transform", open && "rotate-180")} />
        </button>
        <select className="input w-auto py-1 text-xs" aria-label="Yetki" value={m.role} disabled={setRole.isPending}
          onChange={(e) => setRole.mutate(e.target.value as BookRole, onErr)}>
          {ROLES.map((r) => <option key={r.value} value={r.value}>{d.roles?.[r.value] ?? r.label}</option>)}
        </select>
        {!m.joined && <button className="btn-ghost px-2 py-1 text-xs" title="Şifre sıfırla" aria-label={`${m.username} için şifre sıfırla`} disabled={reset.isPending}
          onClick={() => void ask(`${m.username} için şifre sıfırlansın mı?\n\nŞu anki şifresi çalışmaz; ekranda bir kez gösterilecek geçici şifreyi ona iletmen gerekir.`,
            { confirm: "Şifreyi sıfırla", danger: true, irreversible: false }).then((ok) => { if (ok) reset.mutate(undefined, onErr); })}><KeyRound size={14} /></button>}
        <button className="btn-danger px-2 py-1 text-xs" title="Çıkar" aria-label={`${m.username} üyesini çıkar`} disabled={remove.isPending}
          onClick={() => void ask(`${m.username} ortak defterden çıkarılsın mı? Eklediği kayıtlar defterde kalır.`).then((ok) => { if (ok) remove.mutate(undefined, onErr); })}>
          <Trash2 size={14} /></button>
      </div>
      {shown && (
        <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          Geçici şifre (bir kez gösterilir; ilk girişte değiştirilir)
          <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all text-xs">{shown}</code>
            <button className="btn-ghost px-2 py-1" onClick={() => navigator.clipboard.writeText(shown).then(() => ui.toast("Kopyalandı"), () => {})}>
              <Copy size={14} /></button></div>
        </div>
      )}
      {m.seat && (waiting || open) && (
        <div className={cx("mt-2 space-y-1 rounded-lg p-2 text-xs", waiting ? "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200" : "bg-stone-50 text-stone-600 dark:bg-stone-900 dark:text-stone-300")}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1">{seatText(m.seat)}</span>
            {waiting && seated.length > 0 && (
              <button className="btn-primary px-3 py-1 text-xs" disabled={give.isPending} onClick={() => setPick(true)}>Koltuk ver</button>
            )}
          </div>
          {waiting && <p className="opacity-80">Bir koltuk boşalınca (biri ayrılır ya da çıkarılır) kendiliğinden geçer.</p>}
        </div>
      )}
      {pick && (
        <Modal title="Kimin koltuğu?" onClose={() => setPick(false)}>
          <p className="mb-2 text-sm text-stone-500">{short(m.username)} için koltuğunu alacağın üyeyi seç.</p>
          <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
            {seated.map((x) => (
              <li key={x.id}>
                <button className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800/50" disabled={give.isPending} onClick={() => giveFrom(x)}>
                  <span className="min-w-0 flex-1"><b className="break-all">{short(x.username)}</b>
                    <span className="block text-xs text-stone-500">{x.last_login ? `son giriş ${tsDateTR(x.last_login)}` : "henüz girmedi"}</span></span>
                </button>
              </li>
            ))}
          </ul>
          <button className="btn-ghost mt-3 w-full justify-center py-2" onClick={() => setPick(false)}>Vazgeç</button>
        </Modal>
      )}
      {open && (
        <div className="mt-2 space-y-2 rounded-lg bg-stone-50 p-3 dark:bg-stone-900">
          <h4 className="flex items-center gap-1.5 font-medium"><EyeOff size={14} /> Bu kişiden neleri gizle</h4>
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2">
              <input type="radio" name={`rules-${m.id}`} checked={!m.own_rules}
                onChange={() => toShared.mutate(undefined, onErr)} />
              Ortak kuralları kullan
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name={`rules-${m.id}`} checked={m.own_rules}
                onChange={() => setRules.mutate(m.rules, onErr)} /* ortak kurallardan başlar */ />
              Bu kişiye özel
            </label>
          </div>
          {m.own_rules
            ? <RulesEditor d={d} r={m.rules} onChange={(p) => setRules.mutate(p, onErr)} />
            : <p className="text-xs text-stone-500">Aşağıdaki ortak kurallar geçerli{n ? ` (${n} gizli)` : ""}. Değiştirmek için <b>Bu kişiye özel</b>.</p>}
        </div>
      )}
    </li>
  );
}

/** Ayarlar → Ortak defter: aileni davet et, yetkisini ve neyi göremeyeceğini seç. */
export default function BookSettings() {
  const ui = useUI();
  const plan = usePlan();
  const email = useLoginKind() === "email"; // hazır sürüm: üye e-postayla girer
  const q = useQuery({ queryKey: ["book"], queryFn: () => call<Book>("/api/book"), retry: false });
  const [note, setNote] = useState("");
  const [role, setRole] = useState<BookRole>("editor");
  const [openCode, setOpenCode] = useState<string | null>(null); // QR'ı açık davet
  const onErr = { onError: (e: Error) => ui.error(e) };
  const invite = useSave(() => call<Invite>("/api/book/invites", "POST", { note, book_role: role }), (i) => {
    setNote(""); setOpenCode(i.code); ui.toast("Davet oluşturuldu: QR'ı okut ya da bağlantıyı paylaş");
  });
  const delInvite = useSave((code: string) => call(`/api/book/invites/${code}`, "DELETE"), () => ui.toast("Davet iptal edildi"));
  const renew = useSave((code: string) => call<Invite>(`/api/book/invites/${code}/renew`, "POST"), (i) => {
    setOpenCode(i.code); ui.toast("Yeni kod üretildi; eski bağlantı artık çalışmaz");
  });
  const setRules = useSave((r: Partial<Rules>) => call("/api/book/rules", "PUT", r));
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => ui.toast("Kopyalandı"), () => {});
  const d = q.data;
  // yetkisi yok (üye) / eski sunucu: bölüm yok; başka hata ve yükleme görünür
  if (!d) return [403, 404].includes((q.error as { status?: number } | null)?.status ?? 0) ? null
    : <SorguDurumu q={q} etiket="Ortak defter yükleniyor"><KartIskelet satir={3} /></SorguDurumu>;
  const joinable = d.members.length === 0; // kendi üyesi olmayan: başkasının defterine katılabilir (book_join)
  const roleLabel = (r?: string | null) => (r ? d.roles?.[r] ?? ROLES.find((x) => x.value === r)?.label : undefined);
  return (
    <>
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Users2 size={18} /> Ortak defter</h2>
      <p className="text-sm text-stone-500">
        Aileni davet et: her biri kendi {email ? "e-posta adresi" : "kullanıcı adı"} ve şifresiyle girer, <b>bu defteri</b> görür. Ne
        yapabileceğini yetkisi belirler. Gizlediklerin ona hiç görünmez; toplamlara da girmez. Ayrıca kendine ait özel bir defteri olur.
      </p>

      {d.seats && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-stone-50 p-2 text-sm dark:bg-stone-900">
          <span className={cx("inline-flex items-center gap-1.5 font-semibold", d.seats.used >= d.seats.total ? "text-amber-700 dark:text-amber-400" : "text-stone-700 dark:text-stone-200")}>
            <span aria-hidden className="inline-flex gap-0.5">
              {Array.from({ length: d.seats.total }, (_, i) => (
                <span key={i} className={cx("size-2 rounded-full", i < d.seats!.used ? (d.seats!.used >= d.seats!.total ? "bg-amber-500" : "bg-blue-600") : "bg-stone-300 dark:bg-stone-700")} />
              ))}
            </span>
            {d.seats.total} koltuktan {d.seats.used}{suffix(d.seats.used)} dolu
          </span>
          <span className="text-xs text-stone-500">Aile koltuğu: sen + {d.seats.total} kişi. Kendi Premium'u olan koltuk kullanmaz; en çok {d.seats.max_members} üye.
            {d.seats.waiting ? ` ${d.seats.waiting} kişi koltuk bekliyor.` : ""}</span>
        </div>
      )}

      <div className="space-y-2">
        {plan?.features.ortak_defter === false ? (
          <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
            Davet etmek <b>{needLabel(plan, "ortak_defter")}</b> paketinde. Ayarlar → Paketin bölümüne bak.</p>
        ) : (
          <div className="space-y-2">
            <h3 className="font-medium">{d.members.length ? "Birini daha davet et" : "Aileni davet et"}</h3>
            <fieldset className="grid gap-1.5 sm:grid-cols-3">
              <legend className="mb-1 text-xs text-stone-500">Yetki</legend>
              {ROLES.map((r) => (
                <label key={r.value} aria-label={r.label} className={cx("flex cursor-pointer gap-2 rounded-lg border p-2 text-sm",
                  role === r.value ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30" : "border-stone-200 dark:border-stone-700")}>
                  <input type="radio" name="invite-role" className="mt-0.5" checked={role === r.value} onChange={() => setRole(r.value)} />
                  <span><b className="font-medium">{roleLabel(r.value)}</b><span className="block text-xs text-stone-500">{r.sub}</span></span>
                </label>
              ))}
            </fieldset>
            <div className="flex flex-wrap gap-2">
              <input className="input min-w-0 flex-1" placeholder="Not (ör. Annem)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="btn-primary" disabled={invite.isPending} onClick={() => invite.mutate(undefined, onErr)}>Davet oluştur</button>
            </div>
          </div>
        )}
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {d.members.map((m) => <MemberRow key={m.id} m={m} d={d} />)}
          {d.invites.map((i) => (
            <InviteCard key={i.code} inv={i} fallbackLink={link(i.code)} roleLabel={roleLabel(i.book_role)}
              open={openCode === i.code} onToggle={() => setOpenCode(openCode === i.code ? null : i.code)}
              busy={renew.isPending || delInvite.isPending} onCopy={copy}
              onRenew={() => void ask("Bu davet için yeni kod üretilsin mi?\n\nEski bağlantı ve QR artık çalışmaz; yenisini paylaşman gerekir.",
                { confirm: "Yeni kod üret", danger: false }).then((ok) => { if (ok) renew.mutate(i.code, onErr); })}
              onCancel={() => void ask(`Davet iptal edilsin mi?${i.note ? ` (${i.note})` : ""}\n\nBağlantı ve QR artık çalışmaz.`,
                { confirm: "Daveti iptal et", danger: true, irreversible: false }).then((ok) => { if (ok) delInvite.mutate(i.code, onErr); })} />
          ))}
          {!d.members.length && !d.invites.length && <li className="py-2 text-stone-500">Henüz üye yok.</li>}
        </ul>
        {d.members.length > 0 && <p className="text-xs text-stone-500">Üyeye dokun: ondan neleri gizleyeceğini seç.</p>}
      </div>

      <div className="space-y-2 border-t border-stone-100 pt-3 text-sm dark:border-stone-800">
        <h3 className="font-medium">Ortak kurallar: görünmesin</h3>
        <p className="text-xs text-stone-500">Kendine özel kuralı olmayan herkese.</p>
        <RulesEditor d={d} r={d.rules} onChange={(p) => setRules.mutate(p, onErr)} />
        <p className="text-xs text-stone-500">Tek bir işlemi gizlemek için: işleme dokun → <b>Ortak defterde gizle</b>.</p>
      </div>
    </section>
    {joinable && <JoinWithCode />}
    </>
  );
}
