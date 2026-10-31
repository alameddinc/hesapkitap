// Ortak Türkçe onay penceresi (window.confirm yerine): ask(...) Promise<boolean> döner; pencereyi App'teki
// <ConfirmHost /> çizer. Tehlikeli eylem (silme, geri alma) kırmızı düğmeyle; geri dönüşü yoksa "Geri alınamaz" satırı.

export interface AskOptions {
  /** onay düğmesinin adı (varsayılan: metne göre "Sil" / "Geri al" / "Evet") */
  confirm?: string;
  cancel?: string;
  /** kırmızı (tehlikeli) eylem */
  danger?: boolean;
  /** "Geri alınamaz" satırı */
  irreversible?: boolean;
  /** başlık; verilmezse metnin ilk cümlesi */
  title?: string;
}
export interface AskRequest extends Required<Pick<AskOptions, "confirm" | "cancel" | "danger" | "irreversible">> {
  title: string; body: string; resolve: (ok: boolean) => void;
}

let listener: ((r: AskRequest | null) => void) | null = null;
const queue: AskRequest[] = [];

export function onAsk(fn: ((r: AskRequest | null) => void) | null) {
  listener = fn;
  if (fn && queue.length) fn(queue[0]);
}

/** Metinden başlık ve gövde: "\n\n" ya da ilk "?" ile ayrılır. */
function split(message: string): [string, string] {
  const [head, ...rest] = message.split("\n\n");
  const q = head.indexOf("?");
  if (q > 0 && q < head.length - 1) return [head.slice(0, q + 1).trim(), [head.slice(q + 1).trim(), ...rest].filter(Boolean).join("\n\n")];
  return [head.trim(), rest.join("\n\n").trim()];
}

export function ask(message: string, opts: AskOptions = {}): Promise<boolean> {
  const destroy = /silin|çıkarıl|unutul|dönülsün|kapatılsın|geri alın/i.test(message);
  const undo = /geri alın/i.test(message);
  const [t, body] = split(message);
  return new Promise((resolve) => {
    const req: AskRequest = {
      title: opts.title ?? t, body: opts.title ? message : body,
      confirm: opts.confirm ?? (undo ? "Geri al" : /silin/i.test(message) ? "Sil" : /çıkarıl/i.test(message) ? "Çıkar" : "Evet"),
      cancel: opts.cancel ?? "Vazgeç",
      danger: opts.danger ?? destroy,
      irreversible: opts.irreversible ?? (/silin|unutul/i.test(message) && !/geri alınabilir/i.test(message)),
      resolve: (ok) => {
        queue.shift();
        listener?.(queue[0] ?? null);
        resolve(ok);
      },
    };
    queue.push(req);
    if (queue.length === 1) listener?.(req);
    // çizen henüz yoksa istek kuyrukta bekler; ConfirmHost bağlanınca gösterilir
  });
}
