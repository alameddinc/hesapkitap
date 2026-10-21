import NetWorth from "../components/NetWorth";

/** Hesaplar ve kartlar: banka, nakit, yemek/hediye kartları, kart borçları ve net durum. */
export default function Accounts() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Hesaplar ve kartlar</h1>
        <p className="text-sm text-stone-500">
          Bankadaki, cebindeki ve yemek/hediye kartlarındaki paran; kredi kartı borçların ve alacakların. Bakiyeye dokunup düzeltebilirsin.
        </p>
      </div>
      <NetWorth />
    </div>
  );
}
