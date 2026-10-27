import { ArrowLeftRight } from "lucide-react";

/** Mağaza uygulamasının içinden mi açıldık (Capacitor kabuğu user-agent'a ekler). */
export const inApp = () => /HesapKitapApp\//.test(navigator.userAgent);

/** Uygulamanın başlangıç ekranına dön: başka sunucuya bağlan, demodan çık. */
export const toLauncher = () => {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  window.location.href = `${ios ? "capacitor" : "https"}://localhost/?degistir`;
};

/** Ayarlar: sadece mobil uygulamada görünür. */
export default function AppServer() {
  if (!inApp()) return null;
  return (
    <section className="card flex flex-wrap items-center justify-between gap-2 p-4">
      <div>
        <h2 className="font-semibold">Bağlı sunucu</h2>
        <p className="text-sm text-stone-500">{window.location.host}</p>
      </div>
      <button className="btn-ghost border border-stone-300 px-3 py-1.5 text-sm dark:border-stone-700" onClick={toLauncher}>
        <ArrowLeftRight size={14} /> Sunucuyu değiştir
      </button>
    </section>
  );
}
