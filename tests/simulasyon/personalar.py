"""Personalar (uydurma kişiler, tutarlar, kart numaraları, IBAN'lar).

P1 tek maaş 15'i + 3 kart (kesim 13–15, son ödeme 23–25) + kira + kumbara + kişi borcu (yapı canlı kullanıcıya benzer,
   tutarlar uydurma); ay dönümü 17
P2 iki maaş (1'i ve 15'i, aile), ay sonu kesimli kart, otomatik ödemeli fatura, alacak; takvim ayı
P3 kartsız: kira (dekontla), faturalar, nakit ağırlıklı, borç alma (hesaba) + taksitli geri ödeme
P4 kart son ödemesi maaştan önce (maaş 15, kesim ay sonu → son ödeme 10); ay dönümü 15
P5 düzensiz gelir (serbest çalışan): gelir tarihi/tutarı oynak, döküm kipinde iki hesap
P6 emekli (SSK, tahsis günü 20, hafta sonu KAYMAZ), tek kart, ay dönümü 20
"""
from tests.simulasyon.sim import AccCfg, BillCfg, CardCfg, Persona, SalaryCfg

OWNER = "DENEME KİŞİOĞLU"
OWNER_A = "DENEME KISIOGLU"

P1 = Persona(
    key="P1", title="tek maaş + 3 kart + kira + kumbara", seed=101, owner=OWNER, owner_ascii=OWNER_A, cycle_day=17,
    accounts=[AccCfg("H", "Örnek Banka H", start=58000.0, mode="dokum", iban="TR110001200000000000000101"),
              AccCfg("V", "Örnek Banka V", start=34500.0, mode="elle", iban="TR220001500000000000000102"),
              AccCfg("K", "Örnek Banka K", start=3200.0, mode="elle", iban="TR330020500000000000000103"),
              AccCfg("N", "Cüzdan", kind="nakit", start=1500.0)],
    salaries=[SalaryCfg("Maaş", 61500.0, 15, "H")],
    cards=[CardCfg("C1", "5400***0001", 15, 10, "V", buy_mean=700.0),
           CardCfg("C2", "5400***0002", 13, 10, "V", buy_mean=900.0),
           CardCfg("C3", "5200***0003", 14, 11, "K", buy_mean=650.0,
                   styles={"hesaptan": 4, "hesapsiz": 3, "bakiye_once": 2})],
    rent=(18500.0, 5, "V", "elle"),
    bills=[BillCfg("Örnek Elektrik", 640.0, 19, "V"), BillCfg("Örnek Doğalgaz", 1900.0, 22, "V"),
           BillCfg("Örnek İnternet", 520.0, 9, "K", via="kart:C2")],
    jar=("H", 4000.0, 16),
    debts=[{"key": "komsu", "person": "Örnek Komşu", "direction": "borc", "amount": 9000.0, "plan": (6, 10),
            "acc": "V"}],
    transfer_p=0.08,
)

P2 = Persona(
    key="P2", title="iki maaş (1'i ve 15'i), aile", seed=202, owner="ÖRNEK AİLEOĞLU", owner_ascii="ORNEK AILEOGLU",
    cycle_day=1,
    accounts=[AccCfg("A", "Örnek Banka A", start=24000.0, mode="elle", iban="TR440001000000000000000201"),
              AccCfg("B", "Örnek Banka B", start=11000.0, mode="dokum", iban="TR550006200000000000000202"),
              AccCfg("N", "Cüzdan", kind="nakit", start=800.0)],
    salaries=[SalaryCfg("Maaş", 48000.0, 1, "A"), SalaryCfg("Eş maaşı", 39000.0, 15, "B")],
    cards=[CardCfg("C1", "4543***1201", 0, 10, "A", buy_mean=1100.0),
           CardCfg("C2", "4543***1202", 20, 10, "B", buy_mean=800.0, styles={"dokum": 3, "hesapsiz": 1})],
    rent=(24000.0, 7, "A", "dekont"),
    bills=[BillCfg("Örnek Su", 410.0, 12, "A"), BillCfg("Örnek Elektrik", 980.0, 26, "A", via="kart:C1")],
    debts=[{"key": "arkadas", "person": "Örnek Arkadaş", "direction": "alacak", "amount": 6000.0, "acc": "A",
            "due_in": 75, "with_account": True}],
    transfer_p=0.07,
)

P3 = Persona(
    key="P3", title="kartsız: kira, fatura, nakit", seed=303, owner="ÖRNEK KARTSIZ", owner_ascii="ORNEK KARTSIZ",
    cycle_day=1,
    accounts=[AccCfg("A", "Örnek Banka A", start=15000.0, mode="elle", iban="TR660001000000000000000301"),
              AccCfg("N", "Cüzdan", kind="nakit", start=2500.0)],
    salaries=[SalaryCfg("Maaş", 32000.0, 5, "A")],
    rent=(14000.0, 10, "A", "dekont"),
    bills=[BillCfg("Örnek Elektrik", 450.0, 18, "A"), BillCfg("Örnek Su", 260.0, 24, "A"),
           BillCfg("Örnek Telefon", 390.0, 3, "A")],
    biweekly=(600.0, 3),  # 2 haftada bir nakit temizlik ödemesi
    cash_p=0.8, cash_mean=180.0, atm_p=0.1,
    debts=[{"key": "akraba", "person": "Örnek Akraba", "direction": "borc", "amount": 12000.0, "plan": (8, 12),
            "acc": "A", "with_account": True}],
)

P4 = Persona(
    key="P4", title="kart son ödemesi maaştan önce (maaş 15, son ödeme 10)", seed=404, owner="ÖRNEK ERKENOĞLU",
    owner_ascii="ORNEK ERKENOGLU", cycle_day=15,
    accounts=[AccCfg("A", "Örnek Banka A", start=21000.0, mode="elle", iban="TR770001000000000000000401"),
              AccCfg("N", "Cüzdan", kind="nakit", start=600.0)],
    salaries=[SalaryCfg("Maaş", 45000.0, 15, "A")],
    cards=[CardCfg("C1", "5400***0401", 0, 10, "A", buy_mean=1300.0),
           CardCfg("C2", "5400***0402", 28, 12, "A", buy_mean=500.0)],
    rent=(17000.0, 20, "A", "elle"),
    bills=[BillCfg("Örnek Doğalgaz", 1500.0, 8, "A")],
    jar=("A", 2500.0, 16),
)

P5 = Persona(
    key="P5", title="düzensiz gelir (serbest çalışan)", seed=505, owner="ÖRNEK SERBEST", owner_ascii="ORNEK SERBEST",
    cycle_day=1,
    accounts=[AccCfg("A", "Örnek Banka A", start=40000.0, mode="dokum", iban="TR880001000000000000000501"),
              AccCfg("B", "Örnek Banka B", start=5000.0, mode="dokum", iban="TR990006200000000000000502"),
              AccCfg("N", "Cüzdan", kind="nakit", start=1000.0)],
    salaries=[SalaryCfg("Proje geliri", 52000.0, 10, "A", shift="free", var=0.45)],
    cards=[CardCfg("C1", "4543***0501", 22, 10, "B", buy_mean=1000.0, styles={"dokum": 3, "hesaptan": 1})],
    rent=(20000.0, 3, "A", "elle"),
    bills=[BillCfg("Örnek İnternet", 600.0, 14, "B")],
    transfer_p=0.1,
)

P6 = Persona(
    key="P6", title="emekli (SSK, tahsis günü 20, kaymaz)", seed=606, owner="ÖRNEK EMEKLİ", owner_ascii="ORNEK EMEKLI",
    cycle_day=20,
    accounts=[AccCfg("A", "Örnek Banka A", start=8000.0, mode="elle", iban="TR120001000000000000000601"),
              AccCfg("N", "Cüzdan", kind="nakit", start=3000.0)],
    salaries=[SalaryCfg("Emekli aylığı", 23000.0, 20, "A", shift="none", jitter=0.0)],
    cards=[CardCfg("C1", "5400***0601", 5, 10, "A", buy_mean=450.0, buy_p=0.3)],
    bills=[BillCfg("Örnek Elektrik", 380.0, 27, "A"), BillCfg("Örnek Su", 210.0, 2, "A")],
    cash_p=0.6, cash_mean=150.0,
)

ALL = [P1, P2, P3, P4, P5, P6]
