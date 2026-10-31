import { Component, type ReactNode } from "react";

/** Sayfa çizilirken beklenmeyen hata: tüm uygulama beyaz ekrana düşmesin; sayfa yerinde Türkçe uyarı ve yenile. */
export default class PageBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null; key?: string }> {
  state: { error: Error | null; key?: string } = { error: null, key: this.props.resetKey };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: { error: Error | null; key?: string }) {
    return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null; // başka sayfaya geçince sıfırla
  }
  componentDidCatch(error: Error) { console.error("Sayfa hatası", error); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card mx-auto mt-6 max-w-md space-y-3 p-6 text-center" role="alert">
        <h1 className="text-lg font-semibold">Bu sayfa açılırken bir sorun oldu</h1>
        <p className="text-sm text-stone-600 dark:text-stone-300">Verilerin güvende; sayfayı yenilemeyi dene. Sorun sürerse başka bir sayfaya geçip geri gel.</p>
        <button className="btn-primary mx-auto" onClick={() => window.location.reload()}>Sayfayı yenile</button>
      </div>
    );
  }
}
