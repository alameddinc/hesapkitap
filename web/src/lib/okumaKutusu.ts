// Bağlantısız belge okuma (sahip kararı 09.11; mobil ile aynı metin): fiş fotoğrafı / ekstre dosyası bağlantı yokken seçilirse
// okunmaz, cihazda saklanır (IndexedDB; sayfa yenilense de kalır). Bağlanınca kutuda "Şimdi oku" çıkar — okuma hakkı
// sormadan harcanmaz, tek dokunuşla okunur. IndexedDB yoksa (özel mod) bu oturumda bellekte kalır.
import { useSyncExternalStore } from "react";

export interface SakliDosya { blob: Blob; name: string }
export interface BekleyenOkuma { id: string; tur: "fis" | "belge"; files: SakliDosya[]; at: number }

const DB = "hk-okuma";
const STORE = "bekleyen";
let liste: BekleyenOkuma[] = [];
let yuklendi = false;
const subs = new Set<() => void>();
const yay = () => subs.forEach((f) => f());

function ac(): Promise<IDBDatabase | null> {
  return new Promise((res) => {
    try {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => { r.result.createObjectStore(STORE, { keyPath: "id" }); };
      r.onsuccess = () => res(r.result);
      r.onerror = () => res(null);
    } catch { res(null); }
  });
}
async function islem<T>(mod: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | null> {
  const db = await ac();
  if (!db) return null;
  return new Promise((res) => {
    try {
      const tx = db.transaction(STORE, mod);
      const r = fn(tx.objectStore(STORE));
      tx.oncomplete = () => { res(r ? (r.result as T) : null); db.close(); };
      tx.onerror = () => { res(null); db.close(); };
    } catch { res(null); db.close(); }
  });
}

/** Açılışta saklananları oku. */
export async function okumaYukle(): Promise<void> {
  if (yuklendi) return;
  yuklendi = true;
  const all = await islem<BekleyenOkuma[]>("readonly", (s) => s.getAll() as IDBRequest<BekleyenOkuma[]>);
  if (all?.length) { liste = [...all, ...liste.filter((x) => !all.some((a) => a.id === x.id))]; yay(); }
}

/** Bağlantı yokken seçilen belgeyi sakla. */
export async function okumaSakla(tur: BekleyenOkuma["tur"], files: SakliDosya[]): Promise<void> {
  const k: BekleyenOkuma = { id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, tur, files, at: Date.now() };
  liste = [...liste, k];
  yay();
  await islem("readwrite", (s) => { s.put(k); });
}

/** Okundu ya da kullanıcı sildi. */
export async function okumaSil(id: string): Promise<void> {
  liste = liste.filter((x) => x.id !== id);
  yay();
  await islem("readwrite", (s) => { s.delete(id); });
}

export const useBekleyenOkumalar = () => useSyncExternalStore((f) => (subs.add(f), () => { subs.delete(f); }), () => liste);

// "Şimdi oku": Ekstreler sayfası açılınca bekleyen belgeyi alır (fiş ise Ekle penceresi doğrudan alır)
let istek: BekleyenOkuma | null = null;
export const okumaIstegiBirak = (k: BekleyenOkuma) => { istek = k; };
export const okumaIstegiAl = (): BekleyenOkuma | null => { const k = istek; istek = null; return k; };
