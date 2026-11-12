// Yönetici → Ödeme aracıları (components/PaymentGateways.tsx) yardımcıları.
/** Örnek satırı: hazır sürümde tek kişide görülen işyerinin adı gelmez, yalnız sayısı. */
export function ornekMetni(samples: string[], hidden = 0): string | null {
  if (!samples.length) return hidden ? "Örnekler gizli: tek kişide görüldü" : null;
  return `Örnek: ${samples.join(" · ")}${hidden ? ` · ${hidden} işyeri tek kişide görüldü, gizli` : ""}`;
}
