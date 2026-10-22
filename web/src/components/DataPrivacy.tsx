import { Cloud, Code2, Download, EyeOff, FileLock2, Server, ShieldCheck, Sparkles } from "lucide-react";

type Item = { icon: React.ReactNode; text: React.ReactNode };

// Barındırılan hesap: verinin nerede durduğunu ve kimin neyi görebildiğini dürüstçe anlatır.
const HOSTED: Item[] = [
  { icon: <FileLock2 size={16} />, text: <>Verilerin <b>sana ait ayrı bir dosyada</b> tutulur. Bu sunucudaki diğer kullanıcılar göremez.</> },
  { icon: <EyeOff size={16} />, text: <>Uygulamada yöneticinin senin harcamalarını, gelirini ya da hesaplarını gösteren <b>hiçbir ekran yok</b>. Yönetici sadece kullanıcı adını, son giriş tarihini ve kaç fiş okuttuğunu görür.</> },
  { icon: <Server size={16} />, text: <>Dosyan bu sunucuda durur; sunucuyu işleten kişi teknik olarak ona erişebilir. Bu yüzden <b>güvendiğin birinin</b> sunucusunda hesap aç ya da istersen kendi sunucunu kur.</> },
  { icon: <Sparkles size={16} />, text: <>Fiş fotoğrafları ve tanınmayan bankaların ekstreleri, sadece okunmak için <b>Google Gemini</b>'ye gönderilir. Tanınan bankaların ekstreleri sunucuda okunur, dışarı çıkmaz.</> },
  { icon: <Download size={16} />, text: <>Tüm verini istediğin an <b>Ayarlar → Yedekler</b>'den tek dosya olarak indirebilir, kendi sunucuna taşıyabilirsin.</> },
  { icon: <ShieldCheck size={16} />, text: <>Şifren düz metin olarak saklanmaz; geri çevrilemeyecek şekilde şifrelenir.</> },
];

// Kendi sunucusu: veri dışarı çıkmaz; dışarıyla konuşan her şey kullanıcının açtığı bir özelliktir.
const SELF: Item[] = [
  { icon: <Server size={16} />, text: <>Bütün verin <b>bu sunucuda</b>, tek bir dosyada durur. Bize ya da başka bir yere gönderilmez; üyelik, abonelik, reklam yok.</> },
  { icon: <Sparkles size={16} />, text: <>Dışarıyla sadece senin açtığın özellikler konuşur: fiş okuma (<b>Google Gemini</b>), Telegram hatırlatmaları ve e-posta. Hiçbirini açmazsan hiçbir şey dışarı çıkmaz.</> },
  { icon: <Cloud size={16} />, text: <>Telegram'a giden yedek <b>senin şifrenle kilitlenir</b>; şifre olmadan Telegram dahil kimse açamaz.</> },
  { icon: <Code2 size={16} />, text: <>Kodu açık: ne yaptığını herkes <a className="underline" href="https://github.com/alameddinc/hesapkitap" target="_blank" rel="noreferrer">GitHub'da</a> inceleyebilir.</> },
];

/** "Verilerim nerede, kim görebilir?" — kayıt, kurulum ve Ayarlar'da aynı metin. */
export default function DataPrivacy({ mode, collapsible = false }: { mode: "hosted" | "self"; collapsible?: boolean }) {
  const list = (
    <ul className="space-y-2">
      {(mode === "hosted" ? HOSTED : SELF).map((it, i) => (
        <li key={i} className="flex gap-2 text-xs text-stone-600 dark:text-stone-400">
          <span className="mt-0.5 shrink-0 text-green-700 dark:text-green-500">{it.icon}</span>
          <span>{it.text}</span>
        </li>
      ))}
    </ul>
  );
  if (!collapsible) return list;
  return (
    <details className="rounded-lg border border-stone-200 p-2 dark:border-stone-700">
      <summary className="cursor-pointer text-xs font-medium text-stone-700 dark:text-stone-300">
        <ShieldCheck size={14} className="mr-1 inline text-green-700" />Verilerim nerede, kim görebilir?
      </summary>
      <div className="mt-2">{list}</div>
    </details>
  );
}
