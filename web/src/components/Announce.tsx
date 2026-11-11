import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Megaphone, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, call } from "../lib/apiError";
import { ask } from "../lib/ask";
import {
  BASLIK_MAX, formHatasi, gecmisOzeti, hedefOzeti, METIN_MAX, onayMetni, rotaAdi,
  type Duyuru, type DuyuruHedef, type DuyuruPlatform,
} from "../lib/duyuru";
import { cx, dateTimeTR, parseTs } from "../format";
import { useUI } from "../ui";
import SorguDurumu, { SatirIskelet } from "./SorguDurumu";

// Yönetici: telefonlara duyuru (POST /api/admin/announce/preview, POST /api/admin/announce, GET /api/admin/announcements).
// Göndermeden önce hedef sayısı sorulur ve onay istenir; "Önce bana gönder" yalnız yöneticinin kendi telefonlarına.
// Aynı metin 10 dk içinde ikinci kez gönderilirse sunucu 409 duplicate verir: yeniden onayla force.

const KEY = ["admin-announcements"];
type Hedef = { platform: DuyuruPlatform; below_version: string };
type Rota = "ozet" | "store" | "ozel";

export default function Announce() {
  const ui = useUI();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: KEY, retry: false,
    queryFn: () => call<{ items: Duyuru[] }>("GET", "/api/admin/announcements"),
    refetchInterval: (q) => (q.state.data?.items.some((a) => a.status === "sending") ? 3000 : false) });
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [rota, setRota] = useState<Rota>("store");
  const [ozel, setOzel] = useState("#/");
  const [hedef, setHedef] = useState<Hedef>({ platform: "all", below_version: "" });
  const [busy, setBusy] = useState<"test" | "send" | null>(null);
  const route = rota === "store" ? "store" : rota === "ozel" ? ozel.trim() : "";
  const err = formHatasi({ title, body, route, below_version: hedef.below_version });

  // hedef sayısı: seçim değişince (sürüm yazılırken biraz bekleyerek)
  const [h, setH] = useState(hedef);
  useEffect(() => { const t = setTimeout(() => setH(hedef), 350); return () => clearTimeout(t); }, [hedef]);
  const surumOk = !h.below_version.trim() || /^\d+(\.\d+){0,3}$/.test(h.below_version.trim());
  const prev = useQuery({ queryKey: ["admin-announce-preview", h], enabled: surumOk, retry: false,
    queryFn: () => call<DuyuruHedef>("POST", "/api/admin/announce/preview", { platform: h.platform, below_version: h.below_version.trim() || null }) });

  const gonder = async (test: boolean) => {
    if (busy || err) return;
    setBusy(test ? "test" : "send");
    try {
      const target = await call<DuyuruHedef>("POST", "/api/admin/announce/preview",
        { platform: hedef.platform, below_version: hedef.below_version.trim() || null, test_only: test });
      if (!target.devices) { ui.toast(test ? "Bu hesapta kayıtlı telefon yok" : "Bu hedefte telefon yok"); return; }
      if (!(await ask(onayMetni(target, test), { title: test ? "Önce bana gönder" : "Duyuru gönderilsin mi?", confirm: "Gönder" }))) return;
      const payload = { title: title.trim(), body: body.trim(), route: route || null, platform: hedef.platform,
        below_version: hedef.below_version.trim() || null, test_only: test };
      try {
        await call<Duyuru>("POST", "/api/admin/announce", payload);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e;
        if (!(await ask("Bu duyuru 10 dakika içinde zaten gönderildi. Yine de gönderilsin mi?", { title: "Aynı duyuru", confirm: "Yine de gönder" }))) return;
        await call<Duyuru>("POST", "/api/admin/announce", { ...payload, force: true });
      }
      ui.toast(test ? "Deneme duyurusu gönderildi" : "Duyuru gönderiliyor");
      if (!test) { setTitle(""); setBody(""); }
      void qc.invalidateQueries({ queryKey: KEY });
    } catch (e) {
      ui.fail(e);
    } finally {
      setBusy(null);
    }
  };

  const p = prev.data;
  return (
    <>
      <section className="card space-y-3 p-4" aria-labelledby="ann-h" data-testid="duyuru-gonder">
        <h2 id="ann-h" className="flex items-center gap-2 font-semibold"><Megaphone size={18} aria-hidden /> Duyuru gönder</h2>
        <p className="text-xs text-stone-500">Telefonlara bildirim olarak gider. Bildirim ayarında "Duyurular ve yeni sürümler"i kapatana gitmez; sessiz saatteki kişiye sessiz saat bitince ulaşır. Her duyuru kişi başı bir kez.</p>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void gonder(false); }}>
          <div>
            <label className="label" htmlFor="ann-title">Başlık</label>
            <input id="ann-title" className="input w-full" maxLength={BASLIK_MAX} value={title} placeholder="ör. 1.0.5 sürümü geldi"
              onChange={(e) => setTitle(e.target.value)} />
            <span className="mt-0.5 block text-right text-[11px] text-stone-400 tabular-nums">{title.trim().length}/{BASLIK_MAX}</span>
          </div>
          <div>
            <label className="label" htmlFor="ann-body">Metin</label>
            <textarea id="ann-body" className="input min-h-24 w-full" maxLength={METIN_MAX} value={body}
              placeholder="ör. Yeni sürümde fiş okuma daha hızlı. Güncellemek için dokun." onChange={(e) => setBody(e.target.value)} />
            <span className="mt-0.5 block text-right text-[11px] text-stone-400 tabular-nums">{body.trim().length}/{METIN_MAX}</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="min-w-0">
              <label className="label" htmlFor="ann-route">Dokununca</label>
              <select id="ann-route" className="input w-full" value={rota} onChange={(e) => setRota(e.target.value as Rota)}>
                <option value="store">Mağaza sayfası (güncelle)</option>
                <option value="ozet">Uygulama (Özet)</option>
                <option value="ozel">Uygulama içi yol…</option>
              </select>
              {rota === "ozel" && <input className="input mt-1 w-full" aria-label="Uygulama içi yol" value={ozel} placeholder="#/diger/borc-plani"
                onChange={(e) => setOzel(e.target.value)} />}
            </div>
            <div className="min-w-0">
              <label className="label" htmlFor="ann-platform">Telefonlar</label>
              <select id="ann-platform" className="input w-full" value={hedef.platform}
                onChange={(e) => setHedef({ ...hedef, platform: e.target.value as DuyuruPlatform })}>
                <option value="all">Hepsi</option>
                <option value="ios">Yalnız iOS</option>
                <option value="android">Yalnız Android</option>
              </select>
            </div>
            <div className="min-w-0">
              <label className="label" htmlFor="ann-below">Bu sürümün altındakiler</label>
              <input id="ann-below" className="input w-full" inputMode="decimal" value={hedef.below_version} placeholder="ör. 1.0.5 (boş: hepsi)"
                onChange={(e) => setHedef({ ...hedef, below_version: e.target.value })} />
            </div>
          </div>
          <p className="rounded-lg bg-stone-50 px-3 py-2 text-sm dark:bg-stone-800/60" data-testid="duyuru-hedef" aria-live="polite">
            {!surumOk ? "Sürüm 1.0.5 biçiminde olmalı"
              : p ? <><b className="font-medium">{hedefOzeti(p)}</b>{p.opted_out ? <span className="text-stone-500"> · {p.opted_out.toLocaleString("tr-TR")} kişi duyuruları kapatmış</span> : null}
                {hedef.below_version.trim() && <span className="block text-xs text-stone-500">Sürümü bilinmeyen telefonlar da dahil.</span>}</>
                : prev.isLoading ? "Hedef hesaplanıyor…" : "Hedef hesaplanamadı"}
          </p>
          {p && !p.enabled && <p className="text-xs text-amber-800 dark:text-amber-300">Bildirim gönderimi bu sunucuda kapalı (FCM / APNs anahtarı yok): kayıt tutulur ama telefona gitmez.</p>}
          {err && (title || body) && <p className="text-xs text-red-700 dark:text-red-400" role="alert">{err}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost inline-flex items-center gap-1 border border-stone-300 dark:border-stone-700"
              disabled={!!err || !!busy} onClick={() => void gonder(true)}>
              <Eye size={16} aria-hidden /> {busy === "test" ? "Gönderiliyor…" : "Önce bana gönder"}
            </button>
            <button className="btn-primary inline-flex items-center gap-1" disabled={!!err || !!busy}>
              <Send size={16} aria-hidden /> {busy === "send" ? "Gönderiliyor…" : "Gönder"}
            </button>
          </div>
        </form>
      </section>

      <section className="card space-y-2 p-4" aria-labelledby="ann-hist-h">
        <h2 id="ann-hist-h" className="font-semibold">Geçmiş duyurular</h2>
        <SorguDurumu q={list} etiket="Duyurular yükleniyor"><SatirIskelet n={2} /></SorguDurumu>
        {list.data && !list.data.items.length && <p className="py-4 text-center text-sm text-stone-500">Henüz duyuru gönderilmedi.</p>}
        <ul className="divide-y divide-stone-100 dark:divide-stone-800" data-testid="duyuru-gecmis">
          {list.data?.items.map((a) => {
            const d = parseTs(a.created_at);
            return (
              <li key={a.id} className="space-y-0.5 py-2.5">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <b className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">{a.title}</b>
                  {a.test_only && <span className="rounded bg-stone-200 px-1.5 text-[11px] font-medium text-stone-700 dark:bg-stone-800 dark:text-stone-300">deneme</span>}
                  <span className="ml-auto text-xs text-stone-500 tabular-nums">{isNaN(d.getTime()) ? a.created_at : dateTimeTR(d)}{a.admin_name ? ` · ${a.admin_name}` : ""}</span>
                </div>
                <p className="text-sm text-stone-700 [overflow-wrap:anywhere] dark:text-stone-300">{a.body}</p>
                <p className={cx("text-xs [overflow-wrap:anywhere]", a.failed ? "text-amber-800 dark:text-amber-300" : "text-stone-500")}>
                  {gecmisOzeti(a)} · dokununca {rotaAdi(a.route)}</p>
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}
