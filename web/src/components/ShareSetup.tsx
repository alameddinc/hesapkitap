import { useQuery } from "@tanstack/react-query";
import { send } from "../lib/apiError";
import { ask } from "../lib/ask";
import { Copy, Share2, Trash2 } from "lucide-react";
import { useState } from "react";
import { tsDateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import InstallApp from "./InstallApp";

/** Ayarlar: telefondan Paylaş. Android: yüklü uygulama Paylaş menüsünde. iOS: Kısayollar + kişisel anahtar. */
export default function ShareSetup() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["tokens"], queryFn: () => send<
    { id: number; name: string | null; created_at: string; last_used: string | null }[]>("/api/settings/tokens") });
  const [token, setToken] = useState<string | null>(null);
  const create = useSave(() => fetch("/api/settings/tokens", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "iPhone Paylaş" }) }).then((r) => r.json() as Promise<{ token: string }>), (r) => setToken(r.token));
  const del = useSave((id: number) => fetch(`/api/settings/tokens/${id}`, { method: "DELETE" }), () => ui.toast("Anahtar silindi"));
  const url = `${window.location.origin}/api/share`;
  const sc = useQuery({ queryKey: ["shortcut"], queryFn: () => send<{ available: boolean; camera?: boolean }>("/api/share/shortcut") });
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => ui.toast("Kopyalandı"));
  return (
    <section className="card space-y-3 p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Share2 size={18} /> Telefondan paylaş</h2>
      <p className="text-sm text-stone-500">
        Banka uygulamasındaki ekstre/döküm PDF'ini ya da galerideki fiş fotoğrafını Paylaş menüsünden doğrudan gönder.
        Ekstre ve dökümler hemen içe aktarılır; fiş ve faturalar okunur, Özet'te "onay bekliyor" olarak çıkar.
      </p>
      <details className="rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
        <summary className="cursor-pointer font-medium">Android</summary>
        <div className="mt-2"><InstallApp /></div>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-stone-600 dark:text-stone-400">
          <li>Uygulamayı yükle (yukarıdaki düğme; çıkmazsa Chrome ⋮ → <b>Ana ekrana ekle</b>).</li>
          <li>Bir PDF ya da fotoğrafta <b>Paylaş</b> → listede <b>HesapKitap</b>'ı seç. Bu kadar.</li>
        </ol>
      </details>
      <details className="rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700" open>
        <summary className="cursor-pointer font-medium">iPhone (Kısayollar)</summary>
        {sc.data?.available && (
          <div className="mt-2 space-y-2 rounded-lg bg-blue-50/60 p-3 dark:bg-blue-950/20">
            <p className="font-medium">Hızlı kurulum (iPhone'da Safari'den):</p>
            <ol className="list-decimal space-y-1.5 pl-5 text-stone-700 dark:text-stone-300">
              <li>
                <button className="btn-primary px-3 py-1 text-xs" onClick={() => create.mutate(undefined, {
                  onSuccess: (r) => navigator.clipboard.writeText(r.token).then(() => ui.toast("Anahtar kopyalandı"), () => {}) })}>
                  Anahtar oluştur ve kopyala
                </button>
              </li>
              <li>
                <a className="btn-primary inline-flex px-3 py-1 text-xs" href="/hesapkitap.shortcut">Kısayolu ekle</a>
                <span className="block text-xs text-stone-500">Kısayollar açılır → <b>Kısayol Ekle</b> → anahtar sorulunca yapıştır.</span>
              </li>
              <li>Artık PDF ya da fotoğrafta <b>Paylaş → HesapKitap'a gönder</b>.</li>
            </ol>
          </div>
        )}
        {sc.data?.camera && (
          <div className="mt-2 space-y-2 rounded-lg bg-green-50/60 p-3 dark:bg-green-950/20">
            <p className="font-medium">📸 Fiş çek: iki dokunuşla fiş gönder</p>
            <p className="text-xs text-stone-600 dark:text-stone-400">
              Kamerayı açar, çektiğin fişi HesapKitap'a gönderir; okunur ve "Telefondan gelenler"e düşer. Aynı anahtarı kullanır
              (yukarıda oluşturduysan tekrar yapıştır).
            </p>
            <a className="btn-primary inline-flex px-3 py-1 text-xs" href="/hesapkitap-fis.shortcut">"Fiş çek" kısayolunu ekle</a>
            <p className="text-xs text-stone-600 dark:text-stone-400">Sonra en hızlı yere koy:</p>
            <ul className="list-disc space-y-1 pl-5 text-xs text-stone-600 dark:text-stone-400">
              <li><b>Eylem düğmesi</b> (iPhone 15 Pro ve sonrası): Ayarlar → Eylem Düğmesi → Kısayol → <b>Fiş çek</b>.</li>
              <li><b>Kilit ekranı</b>: kilit ekranına basılı tut → Özelleştir → widget ekle → Kısayollar → <b>Fiş çek</b>.</li>
              <li><b>Ana ekran</b>: boş yere basılı tut → + → Kısayollar widget'ı → <b>Fiş çek</b>.</li>
              <li><b>Arkaya dokun</b>: Ayarlar → Erişilebilirlik → Dokunma → Arkaya Dokun → İki Kez → <b>Fiş çek</b>.</li>
            </ul>
          </div>
        )}
        <p className="mt-2 text-xs text-stone-500">{sc.data?.available ? "Olmazsa elle kurulum:" : "Elle kurulum:"}</p>
        <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-stone-600 dark:text-stone-400">
          <li>
            Aşağıdan <b>anahtar oluştur</b> ve kopyala (bir kez gösterilir).
            <div className="mt-1 flex flex-wrap gap-2">
              <button className="btn-primary px-3 py-1 text-xs" onClick={() => create.mutate(undefined)}>Anahtar oluştur</button>
            </div>
            {token && (
              <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-xs dark:border-amber-800 dark:bg-amber-950/40">
                Anahtar (şimdi kopyala, bir daha gösterilmez; kimseyle paylaşma):
                <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all">{token}</code>
                  <button className="btn-ghost px-2 py-1" aria-label="Kopyala" onClick={() => copy(token)}><Copy size={14} /></button></div>
              </div>
            )}
          </li>
          <li><b>Kısayollar</b> uygulaması → <b>+</b> → yeni kısayol, adı: <b>HesapKitap'a gönder</b>.</li>
          <li>Ayarlar (ⓘ) → <b>Paylaşma Ekranında Göster</b>'i aç; kabul edilen türler: <b>Dosyalar, Görüntüler, PDF'ler</b>.</li>
          <li>
            Eylem ekle: <b>URL'nin İçeriğini Al</b>. URL:
            <div className="mt-1 flex items-center gap-2"><code className="flex-1 break-all">{url}</code>
              <button className="btn-ghost px-2 py-1" aria-label="Kopyala" onClick={() => copy(url)}><Copy size={14} /></button></div>
            Yöntem: <b>POST</b> · Başlıklar: <code>Authorization</code> = <code>Bearer ANAHTAR</code> (Bearer, boşluk, anahtar) ·
            İstek gövdesi: <b>Form</b> → alan ekle → <b>Dosya</b>, anahtar adı <code>file</code>, değer: <b>Kısayol Girdisi</b>.
          </li>
          <li>Eylem ekle: <b>Bildirimi Göster</b> → metin: <b>URL'nin İçeriği</b> (sonucu görürsün).</li>
          <li>Artık PDF ya da fotoğrafta <b>Paylaş → HesapKitap'a gönder</b>.</li>
        </ol>
      </details>
      {!!q.data?.length && (
        <ul className="divide-y divide-stone-100 text-xs dark:divide-stone-800">
          {q.data.map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-2 py-1.5">
              <span>{t.name ?? "Anahtar"} · oluşturuldu {tsDateTR(t.created_at)}{t.last_used ? ` · son kullanım ${tsDateTR(t.last_used)}` : " · hiç kullanılmadı"}</span>
              <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Anahtarı sil"
                onClick={() => void ask("Anahtar silinsin mi? Onu kullanan kısayol çalışmaz.").then((ok) => { if (ok) del.mutate(t.id); })}><Trash2 size={13} /></button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
