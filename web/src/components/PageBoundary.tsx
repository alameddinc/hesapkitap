import { Component, type ReactNode } from "react";
import { olayKaydet } from "../lib/olay";
import HataKutusu, { ekranAdi } from "./HataKutusu";

type State = { error: Error | null; key?: string };

/** Sayfa çizilirken beklenmeyen hata: tüm uygulama beyaz ekrana düşmesin; sayfa yerinde Türkçe uyarı, yenile ve
 *  "Yardım al" (lib/destek.ts; hata adı bağlamda). Çökme sorun kaydına gider (lib/olay.ts → ekran_coktu). */
export default class PageBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null, key: this.props.resetKey };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: State) {
    return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null; // başka sayfaya geçince sıfırla
  }
  componentDidCatch(error: Error) {
    console.error("Sayfa hatası", error);
    olayKaydet("ekran_coktu", { detail: `${error.name}: ${error.message}` }); // sorun kaydı (kişisel veri yok; sunucuda süzülür)
  }
  render() {
    if (!this.state.error) return this.props.children;
    const e = this.state.error;
    return (
      <HataKutusu tur="tam" className="mt-6" nerede={ekranAdi()} retry={() => window.location.reload()} retryLabel="Sayfayı yenile"
        aciklama={{ neden: "Bu sayfa açılırken bir sorun oldu", adim: "Verilerin güvende; sayfayı yenilemeyi dene. Sorun sürerse başka bir sayfaya geçip geri gel.",
          kod: "ekran_coktu", ayrinti: `${e.name}: ${e.message}`.slice(0, 300) }} />
    );
  }
}
