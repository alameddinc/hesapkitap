import { useMutation } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useState } from "react";
import Modal from "./Modal";
import Segmented from "./Segmented";

/** Demo: "kurulumsuz sürüm açılınca haber ver" — e-posta, nerede kullanacağı, isteğe bağlı not. */
export default function Waitlist({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [platform, setPlatform] = useState("ikisi");
  const [note, setNote] = useState("");
  const send = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/demo/waitlist", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, platform, note }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Kaydedilemedi");
      return r.json() as Promise<{ new: boolean }>;
    },
  });
  if (send.isSuccess) {
    return (
      <Modal title="Listedesin" onClose={onClose}>
        <div className="space-y-2 py-4 text-center">
          <CheckCircle2 size={40} className="mx-auto text-green-600" />
          <p className="font-medium">{send.data.new ? "Teşekkürler, hazır sürüm açılınca ilk sen öğreneceksin." : "Bu e-posta zaten listede."}</p>
          <p className="text-sm text-stone-500">Arada başka e-posta göndermeyiz.</p>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Kurulumsuz sürüm gelsin mi?" onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!email.includes("@") || send.isPending}
        onClick={() => send.mutate()}>Haber ver</button>}>
      <div className="space-y-3">
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Sunucu kurmadan, kaydolup hemen kullanabileceğin bir sürüm hazırlıyoruz: telefonda uygulama, bilgisayarda web.
          Açılınca haber verelim.
        </p>
        <div>
          <label className="label" htmlFor="wl-email">E-posta</label>
          <input id="wl-email" className="input" type="email" inputMode="email" autoComplete="email" placeholder="ornek@eposta.com"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <div className="label">Nerede kullanırsın?</div>
          <Segmented value={platform} onChange={setPlatform} options={[
            { value: "telefon", label: "Telefon" }, { value: "bilgisayar", label: "Bilgisayar" }, { value: "ikisi", label: "İkisi de" }]} />
        </div>
        <div>
          <label className="label" htmlFor="wl-note">En çok ne için? (isteğe bağlı)</label>
          <input id="wl-note" className="input" maxLength={300} placeholder="ör. kart ekstrelerim, fişler, eşimle ortak bütçe"
            value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {send.error && <p className="text-sm text-red-600">{(send.error as Error).message}</p>}
        <p className="text-xs text-stone-500">
          E-postanı yalnızca bu sürüm açıldığında sana haber vermek için kullanırız; kimseyle paylaşmayız. Listeden çıkmak
          için <a className="underline" href="https://github.com/alameddinc/hesapkitap/issues/new/choose" target="_blank" rel="noreferrer">bize yaz</a>.
        </p>
      </div>
    </Modal>
  );
}
