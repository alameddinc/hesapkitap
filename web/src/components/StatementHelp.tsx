import { BookOpen, ChevronDown } from "lucide-react";
import { useState } from "react";
import { cx } from "../format";

/** Ekstreler: kart ekstresi ve hesap dökümü nereden, nasıl indirilir (bankadan bağımsız genel yol + ipuçları). */
export default function StatementHelp() {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem("hk-stmt-help") !== "0"; } catch { return true; } });
  const toggle = () => { setOpen(!open); try { localStorage.setItem("hk-stmt-help", open ? "0" : "1"); } catch { /* özel mod */ } };
  return (
    <section className="card p-4 text-sm">
      <button className="flex w-full items-center gap-2 text-left font-semibold" onClick={toggle} aria-expanded={open}>
        <BookOpen size={18} /> Ekstre ve hesap dökümü nasıl indirilir?
        <ChevronDown size={16} className={cx("ml-auto text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="font-medium">💳 Kredi kartı ekstresi</h3>
            <ol className="mt-1 list-decimal space-y-1 pl-5 text-stone-600 dark:text-stone-400">
              <li>Bankanın mobil uygulaması ya da internet şubesi → <b>Kartlar</b> → kartını seç.</li>
              <li><b>Ekstreler / Hesap Özeti / Dönem Borcu</b> → dönemi seç → <b>PDF</b> olarak indir ya da <b>Paylaş</b>.</li>
              <li>Bankalar genelde <b>son 12 ayı</b> PDF olarak verir. Daha eski aylar için kart işlemlerini
                <b> "Toplu ekstre" / "Kart hareketleri" Excel</b>'i olarak indir — onu da buraya bırakabilirsin, aylara kendisi böler.</li>
              <li>E-ekstre e-postayla geliyorsa ekteki PDF'i buraya bırak; şifreliyse genelde TC kimlik no'nun ilk ya da son
                haneleri — bir kez sorulur, hatırlanır.</li>
            </ol>
          </div>
          <div>
            <h3 className="font-medium">🏦 Banka hesap dökümü (vadesiz)</h3>
            <ol className="mt-1 list-decimal space-y-1 pl-5 text-stone-600 dark:text-stone-400">
              <li>Mobil uygulama / internet şubesi → <b>Hesaplar</b> → vadesiz hesabını seç.</li>
              <li><b>Hesap Hareketleri</b> → tarih aralığını seç (ilk seferde 1–2 yıl; banka izin verdiği kadar) →
                <b> PDF</b> ya da <b>Excel</b> olarak indir. Tek tek dekont değil, <b>hareket listesi</b> olmalı.</li>
              <li>Maaşın hangi hesaba yatıyorsa önce onu yükle; sonra kart ödemesi yaptığın ve para aktardığın hesapları.</li>
              <li>Aynı ayı iki kez yüklersen sorun olmaz: aynı hareket iki kez eklenmez.</li>
            </ol>
          </div>
          <div className="text-xs text-stone-500 md:col-span-2">
            Telefonda en kolayı: banka uygulamasında PDF'i aç → <b>Paylaş → HesapKitap</b> (Ayarlar → Telefondan paylaş).
            VakıfBank ve Kuveyt Türk ekstreleri ile tanınan hesap dökümleri yapay zekâsız okunur; diğerleri bir kez öğrenilir.
          </div>
        </div>
      )}
    </section>
  );
}
