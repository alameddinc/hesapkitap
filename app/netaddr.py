"""İstemci adresi: hız sınırları ve "yerel ağdan mı?" (kurulum kodu muafiyeti) buna bakar.

Başlıklar istemcinin elindedir; yalnız TCP bağlantısı güvenilir bir vekilden (reverse proxy) geliyorsa okunur:
- HESAPKITAP_TRUSTED_PROXIES: virgüllü CIDR listesi. Verilmezse özel ağlar + loopback (docker ağı, aynı makinedeki
  Caddy/nginx). Boş bırakılırsa ("") hiçbir vekile güvenilmez, her zaman soket adresi kullanılır.
- Vekilden gelen istekte sıra: (HESAPKITAP_TRUST_CF=1 ise) CF-Connecting-IP → X-Real-IP → X-Forwarded-For'un sağdan
  ilk güvenilmeyen öğesi → soket adresi.
- Vekilsiz (doğrudan internete açık) kurulumda istemci başlık uydursa da soket adresi kullanılır.
"""
import ipaddress
import os

_DEFAULT_TRUSTED = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128,fc00::/7"
_cache: tuple[str, list] | None = None


def _trusted_nets() -> list:
    global _cache
    raw = os.environ.get("HESAPKITAP_TRUSTED_PROXIES")
    raw = _DEFAULT_TRUSTED if raw is None else raw
    if _cache is None or _cache[0] != raw:
        nets = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    nets.append(ipaddress.ip_network(part, strict=False))
                except ValueError:
                    pass
        _cache = (raw, nets)
    return _cache[1]


def _ip(v: str | None):
    try:
        return ipaddress.ip_address((v or "").strip())
    except ValueError:
        return None


def is_trusted(v: str | None) -> bool:
    a = _ip(v)
    return a is not None and any(a in n for n in _trusted_nets())


def _xff_client(xff: str) -> str | None:
    """Sağdan ilk güvenilmeyen geçerli adres (her vekil kendi gördüğünü sona ekler; baştakiler uydurulabilir)."""
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for h in reversed(hops):
        if _ip(h) is None:
            return None  # bozuk zincir: daha soldakilere hiç güvenme
        if not is_trusted(h):
            return h
    return hops[0] if hops else None


def candidates(peer: str | None, headers) -> list[str]:
    """Bu istek için başlıklardan çıkarılabilecek istemci adayları (öncelik sırasıyla) + en sonda soket adresi."""
    out: list[str] = []
    if peer and is_trusted(peer):
        if os.environ.get("HESAPKITAP_TRUST_CF") == "1":
            cf = headers.get("cf-connecting-ip")
            if _ip(cf):
                out.append(cf.strip())
        real = headers.get("x-real-ip")
        if _ip(real):
            out.append(real.strip())
        xff = headers.get("x-forwarded-for")
        if xff:
            c = _xff_client(xff)
            if c:
                out.append(c)
    out.append(peer or "?")
    return out


def client_ip(peer: str | None, headers) -> str:
    return candidates(peer, headers)[0]

