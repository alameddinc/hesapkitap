// Sunucu metnindeki **kalın** işaretleri (ör. GET /api/statement-help): parçalara böler, istemci <b> ile yazar.
// HTML yorumlanmaz (dangerouslySetInnerHTML yok): metin olduğu gibi kalır, yalnız ** işaretleri kalın parçayı ayırır.
export const kalinParcalar = (s: string): { text: string; bold: boolean }[] =>
  s.split("**").map((text, i) => ({ text, bold: i % 2 === 1 })).filter((p) => p.text);
