// Fişi alışveriş listesine bağlama (yetenek shopping_receipt_link). Fiş kaydedilince (POST /api/entries yanıtı
// shopping_link) sunucu emin olduğunda kendiliğinden bağlar ("linked" → bildirim), emin değilse "ask": "Bu fiş alışveriş
// listene ait mi?" penceresi (App'teki <ReceiptLinkHost />; kayıt penceresi kapandıktan sonra da açık kalır). Postadan /
// paylaşımdan gelen fişler için liste sayfasında aynı soru şerit olarak (GET /api/shopping-list/receipt-match).
// "Evet, bağla" → POST /link-receipt {entry_id, target, trip_id}; "Hayır" → POST /receipt-dismiss (bir daha sorulmaz).
// Pencereyi X ile kapatmak hiçbir şey göndermez (şerit liste sayfasında yine sorar).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ReceiptText } from "lucide-react";
import { useEffect, useState } from "react";
import { maskText, tl } from "../format";
import { send } from "../lib/apiError";
import { askText, linkedText, type ShoppingAsk, type ShoppingLink, type ShoppingLinked } from "../lib/shopList";
import { useUI } from "../ui";
import Modal from "./Modal";

const money = (n: number) => maskText(tl(n).replace(/,00(?=\s|$)/, ""));
export const RECEIPT_MATCH = ["shopping-receipt-match"] as const;
export const TRIPS = ["shopping-trips"] as const;
export const SAVINGS = ["shopping-savings"] as const;

let listener: ((a: ShoppingAsk | null) => void) | null = null;
let waiting: ShoppingAsk | null = null;

/** Kayıt yanıtındaki shopping_link: bağlandıysa bildirim yazısı (kaydın "Kaydedildi"sine eklenir), sorulacaksa pencere. */
export function handleShoppingLink(link: ShoppingLink | null | undefined): string | null {
  if (!link) return null;
  if (link.status === "linked") return linkedText(link as ShoppingLinked, money);
  if ("entry_id" in link && link.entry_id) {
    waiting = link;
    listener?.(link);
  }
  return null;
}

/** Bağla / hayır: ortak eylemler (pencere ve liste şeridi). */
export function useReceiptActions(onDone?: () => void) {
  const qc = useQueryClient();
  const ui = useUI();
  const refresh = () => {
    for (const k of [["shopping-list"], ["shopping-plan"], ["shopping-optimize"], RECEIPT_MATCH, TRIPS, SAVINGS]) void qc.invalidateQueries({ queryKey: k });
  };
  const link = useMutation({
    mutationFn: (a: ShoppingAsk) => send<ShoppingLinked>("/api/shopping-list/link-receipt", { method: "POST",
      json: { entry_id: a.entry_id, target: a.target, trip_id: a.trip_id ?? null } }),
    onSuccess: (r) => { ui.toast(linkedText({ ...r, status: "linked" }, money)); refresh(); onDone?.(); },
    onError: (e: Error) => { ui.error(e); refresh(); onDone?.(); },
  });
  const dismiss = useMutation({
    mutationFn: (a: ShoppingAsk) => send("/api/shopping-list/receipt-dismiss", { method: "POST", json: { entry_id: a.entry_id } }),
    onSuccess: () => { refresh(); onDone?.(); },
    onError: (e: Error) => ui.error(e),
  });
  return { link, dismiss, busy: link.isPending || dismiss.isPending };
}

function AskBody({ a }: { a: ShoppingAsk }) {
  return <p className="text-sm text-stone-700 dark:text-stone-300" data-testid="fis-soru-metin">{askText(a)} Bağlarsan bu ürünler alındı sayılır ve gerçek fiyatları kaydedilir.</p>;
}

/** App'te bir kez: kayıttan sonra sorulacak fiş. */
export function ReceiptLinkHost() {
  const [a, setA] = useState<ShoppingAsk | null>(waiting);
  useEffect(() => { listener = setA; return () => { listener = null; }; }, []);
  const close = () => { waiting = null; setA(null); };
  const act = useReceiptActions(close);
  if (!a) return null;
  return (
    <Modal title="Bu fiş alışveriş listene ait mi?" onClose={close} footer={<>
      <div className="flex-1" />
      <button type="button" className="btn-ghost" disabled={act.busy} onClick={() => act.dismiss.mutate(a)}>Hayır</button>
      <button type="button" className="btn-primary" disabled={act.busy} onClick={() => act.link.mutate(a)}>Evet, bağla</button>
    </>}>
      <AskBody a={a} />
    </Modal>
  );
}

/** Liste sayfası: son 2 günün bağlanmamış fişi (postadan, paylaşımdan gelen) için aynı soru. */
export function ReceiptAskBanner() {
  const q = useQuery({ queryKey: RECEIPT_MATCH, retry: false, staleTime: 60_000, queryFn: () => send<ShoppingAsk | null>("/api/shopping-list/receipt-match") });
  const act = useReceiptActions();
  const a = q.data;
  if (!a || !a.entry_id) return null;
  return (
    <section className="card flex flex-wrap items-start gap-3 border-blue-200 bg-blue-50/60 p-4 dark:border-blue-900 dark:bg-blue-950/30" data-testid="fis-soru" aria-label="Fiş eşleşmesi">
      <ReceiptText size={22} className="mt-0.5 shrink-0 text-blue-700 dark:text-blue-300" aria-hidden />
      <div className="min-w-0 flex-[1_1_14rem] space-y-1">
        <h2 className="font-semibold">Bu fiş alışveriş listene ait mi?</h2>
        <AskBody a={a} />
      </div>
      <div className="flex w-full justify-end gap-2 sm:w-auto sm:self-center">
        <button type="button" className="btn-ghost" disabled={act.busy} onClick={() => act.dismiss.mutate(a)}>Hayır</button>
        <button type="button" className="btn-primary" disabled={act.busy} onClick={() => act.link.mutate(a)}>Evet, bağla</button>
      </div>
    </section>
  );
}
