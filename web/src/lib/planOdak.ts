// Özet "Dönem sonu" → Planlama: hangi ay açık gelsin (bir kez okunur; Planlama'ya başka yoldan gelince ay kapalı).
let odak: string | null = null;

export const planOdakla = (month: string) => { odak = month; };

export function planOdagi(): string | null {
  const m = odak;
  odak = null;
  return m;
}
