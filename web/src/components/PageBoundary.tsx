import { Component, type ReactNode } from "react";
import FeedbackDialog from "./Feedback";

type State = { error: Error | null; key?: string; report?: boolean };

/** Sayfa çizilirken beklenmeyen hata: tüm uygulama beyaz ekrana düşmesin; sayfa yerinde Türkçe uyarı, yenile ve
 *  "Sorunu bildir" (hata metniyle yöneticiye). */
export default class PageBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null, key: this.props.resetKey };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: State) {
    return p.resetKey !== s.key ? { error: null, key: p.resetKey, report: false } : null; // başka sayfaya geçince sıfırla
  }
  componentDidCatch(error: Error) { console.error("Sayfa hatası", error); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card mx-auto mt-6 max-w-md space-y-3 p-6 text-center" role="alert">
        <h1 className="text-lg font-semibold">Bu sayfa açılırken bir sorun oldu</h1>
        <p className="text-sm text-stone-600 dark:text-stone-300">Verilerin güvende; sayfayı yenilemeyi dene. Sorun sürerse başka bir sayfaya geçip geri gel.</p>
        <div className="flex flex-wrap justify-center gap-2">
          <button className="btn-primary" onClick={() => window.location.reload()}>Sayfayı yenile</button>
          <button className="btn-ghost border border-stone-300 dark:border-stone-700" onClick={() => this.setState({ report: true })}>Sorunu bildir</button>
        </div>
        {this.state.report && (
          <FeedbackDialog context={{ error: String(this.state.error.message || this.state.error), code: "page_error" }}
            onClose={() => this.setState({ report: false })} />
        )}
      </div>
    );
  }
}
