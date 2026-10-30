import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeftRight, BarChart3, ChevronRight, HandCoins, Moon, Landmark, Tag as TagIcon, FileText, LogOut, Coins, Settings2, Sheet, Tags, Zap } from "lucide-react";
import { useUI } from "../ui";
import InstallApp from "../components/InstallApp";
import { inSharedBook, routeVisible, useBook, useMeta } from "../components/hooks";
import ZakatConsent from "../components/ZakatConsent";
import type { Route } from "../ui";

/** Zekât kapalıyken de listede: dokununca sade "açalım mı?" penceresi (ortak defter üyesine ve sade modda gösterilmez). */
function ZakatItem() {
  const ui = useUI();
  const book = useBook();
  const meta = useMeta();
  const [asking, setAsking] = useState(false);
  const off = meta.data?.modules?.zekat === false;
  if (!off) return <Item icon={<Moon size={18} />} label="Zekât ve Sadaka: nisap, kameri yıl, bağışlar" onClick={() => ui.go("zekat")} route="zekat" />;
  if (meta.data?.ui?.simple || inSharedBook(book.data)) return null;
  return (
    <li>
      <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setAsking(true)}>
        <Moon size={18} /><span className="flex-1">Zekât ve Sadaka: nisap, kameri yıl, bağışlar</span>
        <span className="text-xs text-stone-400">Kapalı</span><ChevronRight size={16} className="text-stone-400" />
      </button>
      {asking && <ZakatConsent onClose={() => setAsking(false)} onDone={() => ui.go("zekat")} />}
    </li>
  );
}

function Item({ icon, label, onClick, route }: { icon: React.ReactNode; label: string; onClick: () => void; route?: Route }) {
  const book = useBook(); // ortak defter üyesine kapalı sayfalar ve kapatılan özellikler listelenmez
  const meta = useMeta();
  if (route && (meta.data?.ui?.simple ? route !== "ayarlar" : !routeVisible(book.data, route, meta.data?.modules))) return null;
  return (
    <li>
      <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={onClick}>
        {icon}<span className="flex-1">{label}</span><ChevronRight size={16} className="text-stone-400" />
      </button>
    </li>
  );
}

export default function More() {
  const ui = useUI();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Diğer</h1>
      <InstallApp compact />
      <ul className="card divide-y divide-stone-100 dark:divide-stone-800">
        <Item icon={<BarChart3 size={18} />} label="İstatistik: karşılaştırma, aylık grafik, faiz alarmı" onClick={() => ui.go("istatistik")} route="istatistik" />
        <Item icon={<Sheet size={18} />} label="Tablo: filtreler, pivot, Excel" onClick={() => ui.go("tablo")} route="tablo" />
        <Item icon={<Coins size={18} />} label="Tasarruf: kumbaralar, abonelikler, kısma hedefleri" onClick={() => ui.go("tasarruf")} route="tasarruf" />
        <Item icon={<Landmark size={18} />} label="Hesaplar ve kartlar: banka, nakit, kumbara" onClick={() => ui.go("hesaplar")} route="hesaplar" />
        <Item icon={<Zap size={18} />} label="Faturalar: kurum kurum aylık tutar, değişim" onClick={() => ui.go("faturalar")} route="faturalar" />
        <Item icon={<HandCoins size={18} />} label="Borç / Alacak: TL, döviz, altın" onClick={() => ui.go("borclar")} route="borclar" />
        <ZakatItem />
        <Item icon={<ArrowLeftRight size={18} />} label="Transferler: kime ne gitti" onClick={() => ui.go("transferler")} route="transferler" />
        <Item icon={<TagIcon size={18} />} label="Fiyatlar: fişlerden ürün fiyat takibi" onClick={() => ui.go("fiyatlar")} route="fiyatlar" />
        <Item icon={<FileText size={18} />} label="Ekstreler" onClick={() => ui.go("ekstreler")} route="ekstreler" />
        <Item icon={<Tags size={18} />} label="Kategori kuralları" onClick={() => ui.go("kurallar")} route="kurallar" />
        <Item icon={<Settings2 size={18} />} label="Ayarlar: Telegram, yedekler" onClick={() => ui.go("ayarlar")} route="ayarlar" />
        {status.data?.required && (
          <Item icon={<LogOut size={18} />} label="Çıkış yap" onClick={async () => {
            await fetch("/api/auth/logout", { method: "POST" });
            document.cookie = "hk_book=; path=/; max-age=0; SameSite=Lax"; // ortak/kendi defter seçimi kişiye ait
            qc.invalidateQueries();
          }} />
        )}
      </ul>
    </div>
  );
}

export function LogoutButton() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: () => fetch("/api/auth/status").then((r) => r.json()) });
  if (!status.data?.required) return null;
  return (
    <button className="btn-ghost w-full" onClick={async () => {
      await fetch("/api/auth/logout", { method: "POST" });
      document.cookie = "hk_book=; path=/; max-age=0; SameSite=Lax"; // ortak/kendi defter seçimi kişiye ait
      qc.invalidateQueries();
    }}>
      <LogOut size={16} /> Çıkış{status.data?.user?.username ? ` (${status.data.user.username})` : ""}
    </button>
  );
}
