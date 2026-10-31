// İşlem listesi sayfa sayfa (sunucu b185e16: ?limit&before → {rows, next}; totals=1 kümenin tamamı).
// İlk sayfa hemen çizilir; sonrakiler kaydırdıkça (loadMore) ya da süzgeç/pivot tüm veriyi gerektiriyorsa (auto) gelir.
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { api, type TxnPage } from "../api";

export function usePagedTxns(o: { month?: string; includeDeleted?: boolean; includeEstimates?: boolean; pageSize: number; auto?: boolean }) {
  const q = useInfiniteQuery({
    // "txns" ile başlar: yazma sonrası tüm sorgular tazelenirken bu da tazelenir
    queryKey: ["txns", "paged", o.month ?? "all", !!o.includeDeleted, !!o.includeEstimates, o.pageSize],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api.transactionsPage({ month: o.month, includeDeleted: o.includeDeleted,
      includeEstimates: o.includeEstimates, limit: o.pageSize, before: pageParam, totals: pageParam === null }),
    getNextPageParam: (last: TxnPage) => last.next,
  });
  const { hasNextPage, isFetchingNextPage, fetchNextPage, isError } = q;
  // tüm veri gerekiyorsa (süzgeç, pivot) kalan sayfalar arka arkaya
  useEffect(() => {
    if (o.auto && hasNextPage && !isFetchingNextPage && !isError) void fetchNextPage();
  }, [o.auto, hasNextPage, isFetchingNextPage, isError, fetchNextPage]);
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.rows) ?? [], [q.data]);
  const totals = q.data?.pages[0]?.totals;
  return {
    rows, totals, isLoading: q.isLoading, error: q.error,
    complete: !!q.data && !hasNextPage,
    loadingMore: isFetchingNextPage,
    loadMore: () => { if (hasNextPage && !isFetchingNextPage) void fetchNextPage(); },
  };
}
