import { useQuery } from "@tanstack/react-query";
import { Copy, Share2, Trash2 } from "lucide-react";
import { useState } from "react";
import { dateTR } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

/** Ayarlar: telefondan Paylaş. Android: yüklü uygulama Paylaş menüsünde. iOS: Kısayollar + kişisel anahtar. */
export default function ShareSetup() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["tokens"], queryFn: () => fetch("/api/settings/tokens").then((r) => r.json() as Promise<
    { id: number; name: string | null; created_at: string; last_used: string | null }[]>) });
  const [token, setToken] = useState<string | null>(null);
  const create = useSave(() => fetch("/api/settings/tokens", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "iPhone Paylaş" }) }).then((r) => r.json() as Promise<{ token: string }>), (r) => setToken(r.token));
  const del = useSave((id: number) => fetch(`/api/settings/tokens/${id}`, { method: "DELETE" }), () => ui.toast("Anahtar silindi"));
  const url = `${window.location.origin}/api/share`;
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
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-stone-600 dark:text-stone-400">
          <li>Chrome'da bu siteyi aç → ⋮ menü → <b>Uygulamayı yükle</b> (ya da Ana ekrana ekle).</li>
          <li>Bir PDF ya da fotoğrafta <b>Paylaş</b> → listede <b>HesapKitap</b>'ı seç. Bu kadar.</li>
        </ol>
      </details>
      <details className="rounded-lg border border-stone-200 p-3 text-sm dark:border-stone-700">
        <summary className="cursor-pointer font-medium">iPhone (Kısayollar)</summary>
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
              <span>{t.name ?? "Anahtar"} · oluşturuldu {dateTR(t.created_at.slice(0, 10))}{t.last_used ? ` · son kullanım ${dateTR(t.last_used.slice(0, 10))}` : " · hiç kullanılmadı"}</span>
              <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Anahtarı sil"
                onClick={() => window.confirm("Anahtar silinsin mi? Onu kullanan kısayol çalışmaz.") && del.mutate(t.id)}><Trash2 size={13} /></button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
