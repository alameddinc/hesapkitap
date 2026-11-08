"""Ürün sözlüğü ve iki katlı kategori ağacı (app/product_groups.py kuralları bunu kullanır): "ana › alt" → genel ürünler.

Ana kategoriler product_groups.CATEGORIES'tir (sabit, istemciler ve kayıtlı satırlar bu adları taşır). Alt kategoriler
market raf düzenine göre genel adlardır ("Süt ve Kahvaltılık › Peynir", "Temel Gıda › Bakliyat"); rafı olmayan
kategorilerde (temizlik, içecek, kişisel bakım …) kendi alt adlarımız.

Satır biçimi: "[~][?]Görünen Ad[=YAZILIŞ1,YAZILIŞ2…]"
- Görünen ad genel üründür ("Beyaz Peynir"); adın kendisi de eşleşir (normalize: Türkçe harfsiz BÜYÜK harf).
- "=" sonrası aynı ürünün başka yazılışları (fiş kısaltmaları dahil), virgülle; normalize edilmiş biçimde yazılır.
- "~" yalnız kategori içindir, adla eşleşmez: çeşit kuralının (product_groups.refine) ürettiği ad ("Çikolatalı Süt").
- "?" zayıf ad: başka bir ürünü niteleyen sık kelime ("DOMATES ÇORBASI", "TAVUK ÇEŞNİSİ", "YUMURTA GEZEN TAVUK");
  adda güçlü bir genel ürün de varsa o seçilir. Yazılışın başındaki "?" yalnız o yazılışı zayıf yapar.
"""

TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Süt ve Kahvaltılık": {
        "Süt": [
            "Süt=SUTU,COCUK SUTU", "Kakaolu Süt", "Laktozsuz Süt", "Milkshake", "~Çikolatalı Süt", "~Çilekli Süt",
            "~Muzlu Süt", "~Vanilyalı Süt", "~Karamelli Süt", "~Vişneli Süt", "~Meyveli Süt", "~Kahveli Süt",
            "~Keçi Sütü",
        ],
        "Yoğurt": [
            "Yoğurt=YOGURDU,YOG,BUYUME KUPU", "Yoğurt Mayası=YOGURT MAYA", "Süzme Yoğurt=SUZME YOG",
            "~Meyveli Yoğurt", "~Proteinli Yoğurt",
        ],
        "Ayran ve Kefir": [
            "Ayran", "Kefir", "~Proteinli Kefir",
        ],
        "Peynir": [
            "Beyaz Peynir=BEYAZ PEYNIRI,BEYAZ PEY,INEK PEYNIRI,INEK PEYNIR,EZINE PEYNIRI,EZINE PEYNIR",
            "Kaşar Peyniri=KASAR,KASAR PEYNIR,KASAR PEY,KASARI,KASKAVAL",
            "Eski Kaşar=ESKI KASAR PEYNIRI,ESKI KASAR PEY", "Tulum Peyniri=TULUM,TULUM PEYNIR",
            "Lor=LOR PEYNIRI,LOR PEYNIR", "Labne=LABNE PEYNIRI,LABNE PEYNIR", "Hellim=HELLIM PEYNIRI,HELIM",
            "Krem Peynir=KREM PEYNIRI,SURULEBILIR PEYNIR,UCGEN PEYNIR", "Tost Peyniri=TOST PEYNIR,TOSTLUK PEYNIR",
            "Süzme Peynir=SUZME PEYNIRI", "Keçi Peyniri=KECI PEYNIR", "Koyun Peyniri=KOYUN PEYNIR",
            "Çeçil Peyniri=CECIL,CECIL PEYNIR", "Örgü Peyniri=ORGU,ORGU PEYNIR", "Dil Peyniri=DIL PEYNIR",
            "Çerkez Peyniri=CERKEZ PEYNIR", "Otlu Peynir=OTLU PEYNIRI", "Eritme Peyniri=ERITME PEYNIR,ERITME",
            "Sepet Peyniri=SEPET PEYNIR", "Mihaliç Peyniri=MIHALIC", "Abaza Peyniri", "Mascarpone", "Brie", "Edam",
            "Mozzarella=MOZZARELLA PEYNIRI,MOZARELLA,MOZERELLA", "Cheddar=CHEDDAR PEYNIRI,CEDAR", "Gouda",
            "Parmesan", "Gravyer=GRAVYER PEYNIRI", "Peynir=PEYNIRI,PEY",
        ],
        "Tereyağı ve Margarin": [
            "Tereyağı=TEREYAG,TEREYAGI,TEREYAGLI", "Margarin",
        ],
        "Yumurta": [
            "Yumurta=YUMURTASI,TAVUK YUMURTASI,TAVUK YUMURTA,GEZEN TAVUK YUMURTA,GEZEN TAVUK YUMURTASI",
            "Bıldırcın Yumurtası=BILDIRCIN YUMURTA",
        ],
        "Zeytin": [
            "?Zeytin", "Siyah Zeytin", "Yeşil Zeytin", "Zeytin Ezmesi=SIYAH ZEYTIN EZMESI,YESIL ZEYTIN EZMESI",
            "Zeytin Salatası",
        ],
        "Bal ve Reçel": [
            "Bal=BALI,CICEK BALI", "Petek Bal=PETEK BALI", "Arı Sütü=ARI SUTU", "Reçel=RECELI", "Marmelat=MARMELATI", "Haşhaş Ezmesi",
            "Hurma Ezmesi",
        ],
        "Helva Tahin ve Pekmez": [
            "Tahin=TAHINI", "Helva=HELVASI,TAHIN HELVASI", "Pekmez=PEKMEZI", "Tahin Pekmez=TAHIN PEKMEZ KARISIMI",
        ],
        "Kahvaltılık Gevrek Bar ve Granola": [
            "Mısır Gevreği=MISIR GEVREK,CORN FLAKES",
            "Kahvaltılık Gevrek=KAHVALTILIK GEVREGI,TAHIL GEVREGI,?GEVREK,?GEVREGI", "Granola", "Müsli",
            "Yulaf Ezmesi=YULAF EZME",
            "Bar=BARI,MEYVE BAR,MEYVE BARI,KURUYEMIS BAR,GRANOLA BAR,PROTEIN BAR,PROTEIN BARI,YULAF BAR,TAHILLI BAR,"
            "MEYVELI BAR,MEYVE TOPU",
        ],
        "Sürülebilir Ürünler ve Kahvaltılık Soslar": [
            "Fındık Kreması=FINDIK KREMASI,KAKAOLU FINDIK KREMASI,KAKAOLU KREMA,KAKAOLU KREMASI,KAKAO KREMASI,"
            "KAKAOLU FINDIK EZMESI,KAKAOLU EZME,KAKAOLU EZMESI,KREM CIKOLATA,FINDIK KREMA,KAKAOLU FINDIK KREMA,"
            "KAKAO FINDIK KREMA,KAKAO EZMESI,FISTIK KREMASI,FISTIGI KREMASI,FISTIK KREMA,SUTLU KREMA,KAKAO KREMA",
            "Fındık Ezmesi", "Bisküvi Kreması=BISKUVI KREMA",
            "Fıstık Ezmesi=FISTIK EZMESI,YER FISTIGI EZMESI,FISTIGI EZMESI,KREM FISTIK",
            "Kahvaltılık Sos=KAHVALTILIK SOSU,KAHVALTILIK TATLI SOS,KAHVALTILIK ACI SOS",
        ],
        "Diğer Süt Ürünleri": [
            "Krema=SIVI KREMA,KREMASI SIVI", "Kaymak=KAYMAGI",
            "Bitkisel Süt=BADEM SUTU,BADEM SUT,BADEM ICECEGI,BADEMLI ICECEK,YULAF SUTU,YULAF ICECEGI,YULAFLI ICECEK,SOYA SUTU,"
            "SOYA ICECEGI,HINDISTAN CEVIZI SUTU,HINDISTAN CEVIZI ICECEGI,BITKISEL ICECEK,BITKISEL SUT,FINDIK SUTU,"
            "FINDIK ICECEGI,PIRINC SUTU,HINDI CEVIZI SUTU,HINDI CEVIZI ICECEGI,HINDISTAN CEVIZLI ICECEK",
            "Süt Reçeli=SUT RECELI", "Salep",
        ],
    },
    "Fırın ve Unlu Mamul": {
        "Ekmek ve Unlu Mamüller": [
            "Ekmek=EKMEGI", "Tam Buğday Ekmeği=?TAM BUGDAY,TAM BUGDAY EKMEK,TAM BUGDAYLI EKMEK", "Karabuğday Ekmeği",
            "Tost Ekmeği=TOST EKMEK", "Çavdar Ekmeği=CAVDAR EKMEK,CAVDARLI EKMEK",
            "Hamburger Ekmeği=HAMBURGER EKMEK", "Sandviç Ekmeği=SANDVIC EKMEK", "Lavaş=TORTILLA,DURUM LAVAS",
            "Simit", "Pide", "Bazlama", "Yufka", "Milföy=MILFOY HAMURU", "Grissini", "Galeta", "Poğaça",
            "Börek=BOREGI", "Kruvasan=CROISSANT",
        ],
    },
    "Temel Gıda": {
        "Bakliyat": [
            "Pirinç=PIRINCI", "Basmati Pirinç=BASMATI,BASMATI PIRINCI", "Bulgur=BULGURU", "Mercimek=MERCIMEGI",
            "Kırmızı Mercimek", "Yeşil Mercimek", "Nohut", "Fasulye=FASULYESI,KURU FASULYE", "Barbunya", "Börülce",
            "Kinoa", "Chia Tohumu=CHIA", "Keten Tohumu",
        ],
        "Mantı Makarna ve Erişte": [
            "Makarna=SPAGETTI,SPAGHETTI,PENNE,PENNE RIGATE,FUSILLI,LINGUINE,FETTUCCINE,RIGATONI,TAGLIATELLE,FARFALLE,"
            "ROTINI,PAPPARDELLE,GNOCHETTI",
            "Şehriye", "Erişte", "Lazanya", "Kuskus",
        ],
        "Un ve İrmik": [
            "Un=UNU", "İrmik", "Nişasta=NISASTASI", "Galeta Unu", "Pirinç Unu", "Mısır Unu",
        ],
        "Şeker ve Tatlandırıcılar": [
            "Toz Şeker=TOZ SEKERI", "Küp Şeker=KUP SEKERI", "Şeker=SEKERI", "Pudra Şekeri=PUDRA SEKER",
            "Tatlandırıcı",
        ],
        "Tuz Baharat ve Harçlar": [
            "Tuz=TUZU", "Karabiber=KARA BIBER", "Pul Biber", "Toz Biber", "Kimyon", "Nane", "Kekik", "Sumak",
            "Tarçın", "Zerdeçal", "İsot", "Köri", "Mahlep", "Sarımsak Tozu=TOZ SARIMSAK",
            "Baharat=BAHARATI,CESNI,CESNISI", "Harç=HARCI",
        ],
        "Salça": [
            "Salça=SALCASI,DOMATES SALCASI,DOMATES SALCA", "Biber Salçası=BIBER SALCA",
            "Domates Püresi=DOMATES RENDESI,DOMATES RENDE,RENDELENMIS DOMATES,DOGRANMIS DOMATES",
        ],
        "Pasta Malzemeleri": [
            "Kabartma Tozu", "Vanilin=VANILINI", "Maya=MAYASI", "?Kakao", "Kakao Tozu=TOZ KAKAO", "Krem Şanti=SANTI",
            "Pasta Kreması=PASTACI KREMASI", "Karbonat", "Kuvertür Çikolata=KUVERTUR,DAMLA CIKOLATA,PUL CIKOLATA",
            "Jöle=JEL", "Pasta Süsü", "Hindistan Cevizi=HINDI CEVIZI", "Topping", "Kek Karışımı=KEK KARISIM",
            "Kurabiye Çikolatası=KURABIYE CIKOLATA",
        ],
    },
    "Yağ": {
        "Sıvı Yağlar": [
            "?Ayçiçek Yağı=AYCICEK YAG,AYCICEKYAGI,AYCICEK",
            "?Zeytinyağı=ZEYTINYAG,ZEYTIN YAGI,ZEYTIN YAG,SIZMA,SIZMA ZEYTIN", "Mısır Yağı=MISIR YAG",
            "Fındık Yağı=FINDIK YAG", "Kanola Yağı", "Keten Tohumu Yağı",
        ],
    },
    "Meyve ve Sebze": {
        "Meyve": [
            "?Elma", "Armut", "?Muz", "?Portakal", "Mandalina", "?Limon", "Greyfurt", "Kivi", "?Ananas", "?Mango",
            "Avokado", "?Üzüm", "Karpuz", "Kavun", "?Çilek", "Kiraz", "?Vişne", "?Şeftali", "Nektarin", "Erik",
            "?Kayısı", "Ayva", "?Nar", "?İncir", "Hurma", "Trabzon Hurması", "Yaban Mersini=BLUEBERRY", "Lime",
            "Muşmula", "?Dut", "?Ahududu=FRAMBUAZ", "?Böğürtlen", "Aronya",
        ],
        "Sebze": [
            "?Domates", "Çeri Domates=KOKTEYL DOMATES,CHERRY DOMATES,SEKER DOMATES", "Salatalık", "?Patates",
            "?Soğan=KURU SOGAN", "Taze Soğan", "?Sarımsak", "?Biber", "Sivri Biber", "Çarliston Biber",
            "Kapya Biber", "Dolmalık Biber=DOLMA BIBER", "?Patlıcan=PATLICANI", "?Kabak=SAKIZ KABAK", "?Balkabağı",
            "?Havuç", "Marul", "Kıvırcık", "Roka", "Maydanoz", "Dereotu", "?Ispanak", "Pırasa", "?Lahana",
            "Karnabahar", "Brokoli", "?Mantar=MANTARI,KULTUR MANTARI", "Taze Fasulye", "Turp", "?Pancar", "Zencefil",
            "?Bezelye", "Enginar", "Bamya", "?Mısır", "Salata=ATOM SALATA,GOBEK SALATA", "Semizotu=SEMIZ OTU",
        ],
    },
    "Et, Tavuk ve Balık": {
        "Kırmızı Et": [
            "Kıyma=KIYMASI", "Kuşbaşı", "Köfte=KOFTESI,BURGER,HAMBURGER", "Biftek", "Antrikot", "Pirzola",
            "Dana Eti=DANA ET", "Kuzu Eti=KUZU ET,KUZU LOKUM", "Döner", "Kebap",
        ],
        "Beyaz Et": [
            "?Tavuk=PILIC", "Tavuk But=PILIC BUT,?BUT",
            "Tavuk Göğsü=TAVUK GOGUS,PILIC GOGUS,GOGUS FILETO,TAVUK FILETO", "Tavuk Kanat=KANAT,PILIC KANAT",
            "Tavuk Baget=PILIC BAGET", "Tavuk Pirzola=PILIC PIRZOLA,BUT PIRZOLA", "Tavuk Döner=PILIC DONER",
            "Tavuk Köfte=PILIC KOFTE,PILIC KASAP KOFTE", "Tavuk Kebap=PILIC KEBAP",
            "Tavuk Burger=PILIC BURGER,CITIR BURGER", "Tavuk Sarma", "Tavuk Kuşbaşı", "Nugget",
            "Şinitzel=SNITZEL,SCHNITZEL,SINITZEL",
            "Hindi Eti=?HINDI,HINDI GOGUS,HINDI FILETO,HINDI BUT,HINDI BONFILE,HINDI PIRZOLA",
        ],
        "Deniz Ürünleri": [
            "Ton Balığı=TON BALIK,TON", "Somon=SOMONU", "Levrek=LEVREGI", "Çipura", "Hamsi", "Uskumru", "Sardalya",
            "Palamut", "Mezgit", "Karides", "Kalamar", "?Midye", "?Balık=BALIGI", "Surimi", "Havyar",
            "Ançüez=ANCUEZ", "Panga",
        ],
        "Sakatat": [
            "Ciğer=CIGERI,KARACIGER", "Kokoreç", "İşkembe",
        ],
    },
    "Şarküteri": {
        "Şarküteri": [
            "Sucuk=SUCUGU", "Salam=SALAMI", "Sosis=SOSISI", "Pastırma", "Jambon", "Kavurma=KAVURMASI",
            "Füme Et=HINDI FUME,DANA FUME,HINDI GOGUS FUME,PILIC FUME,TAVUK FUME", "Bacon",
        ],
    },
    "Su ve İçecek": {
        "Su ve Maden Suyu": [
            "Su=DOGAL KAYNAK SUYU,KAYNAK SUYU,ICME SUYU", "Maden Suyu=MADEN SU", "Soda",
        ],
        "Gazlı İçecek": [
            "Kola=COLA,KOLALI GAZLI ICECEK", "Gazoz", "Portakallı Gazoz=PORTAKAL AROMALI GAZOZ",
            "Limonlu Gazoz=LIMON AROMALI GAZOZ",
        ],
        "Meyve Suyu": [
            "Meyve Suyu=PORTAKAL SUYU,ELMA SUYU,VISNE SUYU,NEKTAR,MEYVE NEKTARI", "Şalgam=SALGAM SUYU",
        ],
        "Diğer İçecekler": [
            "Protein İçeceği", "Soğuk Çay=ICE TEA,BUZLU CAY", "Enerji İçeceği", "Limonata", "Şerbet=SERBETI",
            "Soğuk Kahve=BUZLU KAHVE,SOGUK KAHVELER",
        ],
    },
    "Çay ve Kahve": {
        "Çay": [
            "Çay=DOKME CAY", "Poşet Çay=DEMLIK POSET,BARDAK POSET,POSET CAY,DEMLIK POSET CAY,BARDAK POSET CAY",
            "Bitki Çayı=ADACAYI,ADA CAYI,IHLAMUR,YESIL CAY,BEYAZ CAY,FIT CAY,FORM CAYI,MEYVE CAYI,KUSBURNU CAYI",
        ],
        # Kahve çeşidi ayrı gruptur (product_groups.coffee_form: addaki kelime, boyun türü, resmî raf etiketi): çekirdek,
        # öğütülmüş / filtre, Türk kahvesi, granül (kavanoz), tek kullanımlık karışım (3ü1 arada, cappuccino), kapsül,
        # soğuk / hazır kahve içeceği (ml). "Kahve" çeşidi belli olmayan addır (kafe fişi "KAHVE", "Kahve 100 g").
        "Kahve": [
            "Kahve", "Çekirdek Kahve=KAHVE CEKIRDEGI,CEKIRDEK KAHVELER", "Filtre Kahve=OGUTULMUS KAHVE,FILTRE KAHVESI",
            "Türk Kahvesi=TURK KAHVE", "Hazır Türk Kahvesi", "Hazır Kahve=GRANUL KAHVE,INSTANT KAHVE,COZUNEBILIR KAHVE",
            "Kahve Karışımı=KAHVE KARISIMI,KARISIM KAHVE,KARISIMLI KAHVE,?ARADA", "Kapsül Kahve=KAPSUL KAHVE,KAHVE KAPSULU",
            "Kahve Kreması=KAHVE KREMASI,KAHVE KREMALARI",
        ],
    },
    "Kafe": {
        "Kahve İçecekleri": [
            "Latte", "Americano", "Espresso", "Cappuccino=CAPPUCINO,KAPUCINO,KAPUCCINO",
        ],
    },
    "Atıştırmalık": {
        "Bisküvi ve Gofret": [
            "Gofret", "Bisküvi=BISKUVISI", "Kraker", "Kek", "Kurabiye",
        ],
        "Çikolata": [
            "?Çikolata=CIKOLATASI",
        ],
        "Cips ve Çerez": [
            "Cips=CIPSI,PATATES CIPSI", "Kuruyemiş", "?Fındık", "?Ceviz=CEVIZ ICI", "?Badem",
            "?Fıstık=YER FISTIGI,ANTEP FISTIGI", "Leblebi", "Çekirdek=AY CEKIRDEGI,KABAK CEKIRDEGI",
            "Patlamış Mısır=PATLATMALIK MISIR", "Susam",
        ],
        "Şekerleme": [
            "Sakız", "Lokum", "Jelibon", "Şekerleme",
        ],
    },
    "Dondurma ve Tatlı": {
        "Dondurma ve Tatlı": [
            "Dondurma", "Puding", "Baklava", "Sütlaç",
        ],
    },
    "Sos ve Baharat": {
        "Ketçap Mayonez Sos ve Sirkeler": [
            "Ketçap", "Mayonez", "Hardal", "Sirke=SIRKESI", "Soya Sosu", "Acı Sos=ACI BIBER SOSU",
            "Barbekü Sos=BARBEKU SOSU,BBQ SOS", "Makarna Sosu", "Limon Sosu=LIMON SUYU", "Nar Ekşisi", "Sos=SOSU",
        ],
    },
    "Konserve ve Hazır Gıda": {
        "Konserve": [
            "?Konserve=KONSERVESI", "Közlenmiş Biber", "Közlenmiş Patlıcan", "Pilaki=BARBUNYA PILAKI",
            "Haşlanmış Nohut", "Haşlanmış Fasulye", "Zeytinyağlı Fasulye", "Garnitür", "Sarma=YAPRAK SARMA,SARMASI",
        ],
        "Turşu": [
            "Turşu=TURSUSU,KORNISON",
        ],
        "Hazır Yemekler": [
            "Pizza", "Noodle", "Lahmacun", "Çiğ Köfte", "Hazır Yemek=YEMEGI", "Sushi=MAKI", "Kemik Suyu=TAVUK SUYU",
            "Sandviç=SANDVIC", "Patlıcan Salatası=PATLICAN SALATA", "Humus", "Haydari", "Şakşuka", "Mıhlama=KUYMAK",
        ],
        "Hazır Gıda Karışımları": [
            "Çorba=CORBASI", "Bulyon", "Tarhana=TARHANASI", "Toz Salep", "Sıcak Çikolata", "Patates Püresi",
        ],
    },
    "Dondurulmuş Gıda": {
        "Dondurulmuş Gıda": [
            "Mantı=MANTISI,TORTELLONI,RAVIOLI,TORTELLINI",
            "Dondurulmuş Patates=DONDURULMUS PATATES,CITIR PATATES,PARMAK PATATES",
        ],
    },
    "Bebek": {
        "Bebek Mamaları": [
            "Mama=MAMASI,BEBEK MAMASI,EK BESIN", "Devam Sütü=DEVAM SUTU,BEBEK SUTU",
            "Bebek Bisküvisi=BEBE BISKUVISI,BEBEK BISKUVI,BEBE BISKUVI", "Meyve Püresi=PURESI,MEYVE PURE",
        ],
        "Bebek Bezi ve Bakım": [
            "Bebek Bezi=BEBEK BEZ",
        ],
    },
    "Evcil Hayvan": {
        "Kedi": [
            "Kedi Maması=KEDI MAMA", "Kedi Kumu",
        ],
        "Köpek": [
            "Köpek Maması=KOPEK MAMA",
        ],
    },
    "Temizlik": {
        "Çamaşır": [
            "Deterjan", "Sıvı Deterjan=JEL DETERJAN", "Toz Deterjan",
            "Kapsül Deterjan=KAPSUL DETERJAN,TABLET DETERJAN,CAMASIR KAPSULU,DETERJAN KAPSULU", "Yumuşatıcı",
            "Çamaşır Suyu",
        ],
        "Bulaşık": [
            "Bulaşık Deterjanı=BULASIK DETERJAN", "Bulaşık Tableti=BULASIK MAKINESI TABLETI",
            "Bulaşık Makinesi Deterjanı=BULASIK MAKINESI DETERJANI,BULASIK MAKINESI JELI,BULASIK MAKINESI JEL DETERJAN",
        ],
        "Ev Temizliği": [
            "Yüzey Temizleyici", "Çöp Poşeti=COP TORBASI",
        ],
    },
    "Ev ve Mutfak": {
        "Mutfak Gereçleri": [
            "Filtre Kağıdı=KAHVE FILTRE KAGIDI,FILTRE KAHVE KAGIDI,KAHVE FILTRESI,FILTRE KAGIT",
        ],
    },
    "Kağıt Ürünleri": {
        "Kağıt Ürünleri": [
            "Tuvalet Kağıdı=TUVALET KAGIT", "Kağıt Havlu=HAVLU KAGIT,KAGIT HAVLUSU", "Peçete", "Islak Mendil",
        ],
    },
    "Kişisel Bakım": {
        "Saç Bakımı": [
            "Şampuan", "Saç Kremi",
        ],
        "Ağız ve Diş Bakımı": [
            "Diş Macunu=DIS MACUN", "Diş Fırçası",
        ],
        "Vücut Bakımı": [
            "Duş Jeli", "Sabun", "Sıvı Sabun", "Deodorant",
        ],
    },
    "Akaryakıt": {
        "Akaryakıt": [
            "Motorin=DIZEL", "Benzin=KURSUNSUZ BENZIN,KURSUNSUZ", "LPG=OTOGAZ",
        ],
    },
    "Tütün": {
        "Tütün": [
            "Sigara",
        ],
    },
    "Alkollü İçecek": {
        "Alkollü İçecek": [
            "Bira", "Şarap", "Rakı",
        ],
    },
}

# Genel adın yanında grubu değiştirmeyen nitelik kelimeleri (çeşit, işlem, köken, kalibre, ambalaj dışı raf dili).
# product_groups.MODIFIERS ile birlikte: adda genel üründen başka yalnız bunlar varsa kural eşleşir; bilinmeyen kelime
# (çoğu zaman tanınmayan bir marka) adı yapay zekâya bırakır.
QUALIFIERS = {
    # süt ürünleri
    "HOMOJENIZE", "OLGUNLASTIRILMIS", "SUZME", "PROBIYOTIK", "KAYMAKLI", "KAYMAKSIZ", "YAYIK", "KOY", "INEK",
    "KOYUN", "MANDA", "ESKI", "OTLU", "TOST", "KREM", "SURULEBILIR", "UCGEN", "YAGSIZ", "YAG", "YRM", "YGL",
    "AROMALI", "FERMENTE", "QUARK", "COMLEK", "TAVA",
    # zeytin, yağ
    "SALAMURA", "KALIBRE", "XS", "XL", "XXL", "SELE", "CIZIK", "KIRMA", "DOLGULU", "BIBERLI", "SIZMA", "NATUREL",
    "NATURAL", "RIVIERA", "SIKIM", "DOMAT", "GEMLIK", "EDREMIT", "AYVALIK", "TRILYE",
    # bal, kahvaltılık
    "CICEK", "ILAVESIZ", "GELENEKSEL", "KAHVALTILIK", "TAHILLI", "YULAFLI", "GLUTENSIZ", "VITAMINLI", "KEPEKLI",
    "TOHUMLU", "SUSAMLI", "FIRINDAN", "TANDIR", "CAVDARLI", "BUGDAY", "CAVDAR", "BUGDAYLI", "KARISIK", "MIX",
    # bakliyat, makarna, un
    "ESMER", "BALDO", "OSMANCIK", "PILAVLIK", "KOFTELIK", "MEKSIKA", "HASLANMIS", "BURGU", "FIYONK", "BONCUK",
    "YUKSUK", "DIRSEK", "KELEBEK", "KESME", "YASSI", "RIGATE", "ARPA", "TEL", "NO", "INSTANT", "KURU", "TOZ", "IYOTLU",
    "IYOTSUZ", "SOFRA", "KAYA", "DENIZ", "PAPATYA", "SPIRAL", "YILDIZ", "BRONZ", "KALIP", "BATTAL", "BELUGA",
    "BASBASI", "PIKNIK", "HIMALAYA", "SEYLON", "KAYNAK", "DEGIRMEN", "TANE", "OGUTME", "OGUTULMUS", "SOFRADA",
    "DUNYA", "HAMUR", "BESIN", "STEVIA", "PREBIYOTIK", "ALMAN", "TIPI", "JALAPENO", "DLM", "BUN", "MENEMEN",
    "BALZAMIK", "BALSAMIK", "KASIK", "PROSYNEO", "COMBIOTIC", "COMBIOTIK", "OPTIPRO", "COMFORT", "ERITME", "SAVAK",
    "YORUK", "DERI", "VAN", "MALKARA", "PORSIYON", "TOPLARI", "TOPU", "MARBLE", "TRUFF", "ALYANAK", "URUNU",
    # et, tavuk, balık, şarküteri
    "DONDURULMUS", "DONUK", "IZGARA", "FUME", "KEMIKSIZ", "KEMIKLI", "DERILI", "DERISIZ", "PISMIS", "HAZIR",
    "KANGAL", "ISIL", "ISLEM", "GORMUS", "MACAR", "KOKTEYL", "BATON", "PARMAK", "KASAP", "DANA", "KUZU", "HINDI",
    "KALCALI", "KANATLI", "TEMIZLENMIS", "CITIR", "ACILI", "BAHARATLI", "SOSLU", "TEREYAGLI", "KASARLI",
    "KABURGA", "SIRT", "CEMENI", "CEMENSIZ", "BUFE", "ITALYAN", "FRANKFURTER", "UST", "ALT", "KOK", "INCIK", "SIS",
    "COP", "FILETO", "STEAK", "GLAZE", "GLAZELI", "PANE", "HALKA", "LAKERDA", "SOTE", "CIZBIZ", "COBAN", "CIFTLIK",
    "CIFTLIGI", "KROKET",
    # meyve, sebze
    "SALKIM", "DEMET", "DOKME", "STARKING", "GOLDEN", "GRANNY", "SMITH", "AMASYA", "DEVECI", "SANTA", "MARIA",
    "CEKIRDEKSIZ", "CEKIRDEKLI", "PEMBE", "KOZLENMIS", "DOLMALIK", "KIZARTMALIK", "SIVRI", "CARLISTON", "KAPYA",
    "RED", "GLOBE", "ANJELIK", "GALA", "VALENCIA", "KANLI", "CENGELKOY", "KESTANE", "BOROZAN", "MOR", "TOPAK",
    "TURUNCU", "DIZI", "SARI", "KARAKOVAN", "GUL", "BERGAMOT", "TURUNC", "KABUGU", "CUBUK",
    # tat, işlem
    "AROMA", "SEKERLI", "ACI", "TATLI", "EKSI", "PREMIUM", "GURME", "GOURMET", "BIO", "VEGAN", "KLASIK", "ROYAL",
    "GOLD", "PLUS", "ORIGINAL", "TEK", "CIFT", "YOGUN", "LEZZET", "LEZZETI", "LEZZETLERI", "SMOKY", "HAM", "SALGI",
    "DETOX", "DETOKS", "JUMBO", "MEGA", "MAXI", "LUKS", "LUX", "FIT", "FORM", "DENGE", "NESIL", "SPECIAL", "SPECIALE",
    "CESIT", "CESITLERI", "PROFESYONEL", "BARISTA", "KIDS", "KIDZ", "VE", "ILE", "YAS", "ORMAN", "MEYVELER",
    "VANILYA", "PESTO", "SPREY", "YUMUSAK", "DOLGUN", "GUCLU", "SEF", "GIBI", "ANATOLIA", "ET", "SECME", "OMEGA",
    # kahve, çay, deterjan çeşidi (product_groups.coffee_form / tea_form / detergent_form bunlara bakar)
    "ARADA", "KAPSUL", "CAPSULE", "GRANUL", "COZUNEBILIR", "STICK", "LUNGO", "INTENSO", "CREMA", "BLEND", "ORIGINS",
    "ORIGIN", "MOCHA", "MACCHIATO", "XPRESS", "ICEPRESSO", "DECAF", "ARABICA", "ROBUSTA", "CLASSIC", "BARDAK", "TABLET",
    "MOCHACCINO", "FRAPPE", "SI", "IN",
    # yöre
    "ANTEP", "ERZINCAN", "KARS", "IZMIR", "INEGOL", "TEKIRDAG", "AKCAABAT", "MARDIN", "URFA", "MARAS", "KAYSERI",
    "TRAKYA", "ANTAKYA", "BURSA", "ADANA", "ISTANBUL", "KARADENIZ", "EGE", "NORVEC",
}

# Ambalaj ve raf dili (atılır; product_groups.DESCRIPTORS'a eklenir)
PACKAGING = {"KASE", "KAVANOZ", "TENEKE", "BIDON", "KOVA", "VAKUMLU", "TABAK", "TABAKLI", "POSETLI", "FOLYO", "TUP",
             "CITKAPAK", "KAPAK", "KAPAKLI", "SET", "SETI", "SERI", "SERISI", "MAP", "SKINPACK", "FILE", "CM", "GRAM",
             "KILO", "LITRE", "ADETLI", "PAKETLI", "DOYPACK", "POUCH", "SISESI", "KUTUSU", "MULTIPACK", "KAP",
             "TABAGI", "HEDIYELI", "HEDIYE"}

# Marka → genel ürün (küçük düzeltme katmanı; asıl dayanak katalog, app/product_catalog.py): adı yalnız markayla anılan
# ürünler ("COLA TURKA 330ML", "FANTA 1L", "NESCAFE GOLD"). Adda marka öbeğinden başka güçlü bir genel ürün varsa o
# seçilir ("NUTELLA BİSKÜVİ" → Bisküvi). Anahtar normalize (Türkçe harfsiz BÜYÜK) marka öbeği; değer sözlükteki genel ad.
BRAND_PRODUCTS: dict[str, str] = {
    "COCA COLA": "Kola", "COCACOLA": "Kola", "COLA TURKA": "Kola", "PEPSI": "Kola", "PEPSI COLA": "Kola",
    "PEPSI MAX": "Kola", "LE COLA": "Kola", "COKE": "Kola",
    "FANTA": "Portakallı Gazoz", "YEDIGUN": "Portakallı Gazoz", "SPRITE": "Limonlu Gazoz", "7UP": "Limonlu Gazoz",
    "7 UP": "Limonlu Gazoz", "SEVEN UP": "Limonlu Gazoz",
    "ICE TEA": "Soğuk Çay", "ICETEA": "Soğuk Çay", "FUSE TEA": "Soğuk Çay", "FUSETEA": "Soğuk Çay",
    "NESTEA": "Soğuk Çay", "LIPTON ICE": "Soğuk Çay",
    "NESCAFE": "Kahve", "JACOBS": "Kahve",
    "RED BULL": "Enerji İçeceği", "REDBULL": "Enerji İçeceği", "MONSTER": "Enerji İçeceği", "BURN": "Enerji İçeceği",
    "NUTELLA": "Fındık Kreması", "SARELLE": "Fındık Kreması",
    "CAPPY": "Meyve Suyu", "MEYSU": "Meyve Suyu",
}
