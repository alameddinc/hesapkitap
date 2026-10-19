import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: api.meta });

/** Her yazma işleminden sonra tüm sorguları tazele: veri küçük, basitlik daha değerli. */
export function useSave<A, R>(fn: (a: A) => Promise<R>, onDone?: (r: R) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      qc.invalidateQueries();
      onDone?.(r);
    },
  });
}
