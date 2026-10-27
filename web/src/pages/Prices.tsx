import { PlanGate } from "../components/Plan";
import PriceWatch from "../components/PriceWatch";

/** Fişlerden ürün fiyat takibi: aynı ürünü kaça almıştın, şimdi kaça alıyorsun. */
export default function Prices() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Fiyatlar</h1>
        <p className="text-sm text-stone-500">Kişisel enflasyonun: fişini okuttuğun ürünlerin birim fiyatı zaman içinde.</p>
      </div>
      <PlanGate feature="fiyatlar" title="Fiyat takibi"><PriceWatch /></PlanGate>
    </div>
  );
}
