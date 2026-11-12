// "Yardım al" penceresi (lib/destek.ts ince arayüzü): App'te bir kez. Şimdilik "Sorun bildir" penceresi bağlamla açılır;
// destek sohbeti birleşince bu dosya da o uygulamayla değişebilir.
import { destekKapat, useDestek } from "../lib/destek";
import FeedbackDialog from "./Feedback";

export default function DestekKapisi() {
  const d = useDestek();
  if (!d) return null;
  return (
    <FeedbackDialog key={d.no} title="Yardım al" onClose={destekKapat}
      context={{ error: [d.nerede, d.ayrinti].filter(Boolean).join(": "), code: d.kod }} />
  );
}
