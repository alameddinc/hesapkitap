"""Uydurma "yazı tipi kodlu" PDF: metin Type3 yazı tipiyle çizilir, ToUnicode yok, glif adları anlamsız ("C1a"), kodlar
belgedeki ilk görünme sırasıyla verilir (gerçek Akbank ekstresinin yapısı; içerik tamamen uydurma). pdfminer bu
metni "(cid:N)" olarak okur. Glifler Pillow'un yazı tipinden çizilmiş piksel satırları (dikdörtgen): aynı karakter
her belgede aynı çizim programı."""
import io

from PIL import Image, ImageDraw, ImageFont

EM = 40          # glif çizim çözünürlüğü (piksel / em)
SIZE = 9         # punto
LEAD = 13        # satır aralığı


def _font(path: str | None):
    return ImageFont.truetype(path, EM) if path else ImageFont.load_default(EM)


def glyph_program(ch: str, font_path: str | None = None) -> tuple[bytes, int]:
    """(CharProc baytları, genişlik) — glif alanı piksel, taban çizgisi y=0."""
    font = _font(font_path)
    width = max(1, round(font.getlength(ch))) if ch != " " else EM // 3
    if ch == " ":
        return f"{width} 0 d0\n".encode(), width
    asc, desc = font.getmetrics()
    im = Image.new("L", (width + 4, asc + desc), 0)
    ImageDraw.Draw(im).text((0, 0), ch, font=font, fill=255)
    px = im.load()
    ops = [f"{width} 0 d0"]
    for y in range(im.height):
        x = 0
        while x < im.width:
            if px[x, y] > 127:
                s = x
                while x < im.width and px[x, y] > 127:
                    x += 1
                ops.append(f"{s} {asc - y - 1} {x - s} 1 re")
            else:
                x += 1
    ops.append("f")
    return ("\n".join(ops) + "\n").encode(), width


def make(lines: list[str], font_path: str | None = None, per_page: int = 55, seed_order: str = "") -> bytes:
    """Satırları Type3 yazı tipiyle A4 sayfalara yaz. seed_order: kod sırasını değiştirmek için önce sayılacak
    karakterler (aynı glifler farklı belgelerde farklı kodla — gerçekteki gibi)."""
    codes: dict[str, int] = {}
    for ch in seed_order + "".join(lines):
        if ch not in codes:
            codes[ch] = len(codes) + 1
    assert len(codes) < 255
    progs = {ch: glyph_program(ch, font_path) for ch in codes}

    objs: list[bytes] = []

    def add(b: bytes | str) -> int:
        objs.append(b.encode("latin-1") if isinstance(b, str) else b)
        return len(objs)

    def stream(data: bytes, extra: str = "") -> int:
        return add(f"<< /Length {len(data)} {extra}>>\nstream\n".encode() + data + b"\nendstream")

    procs = {ch: stream(p) for ch, (p, _) in progs.items()}
    names = {ch: f"C{codes[ch]:02x}" for ch in codes}
    diffs = " ".join(f"{codes[ch]} /{names[ch]}" for ch in sorted(codes, key=codes.get))
    first, last = 1, max(codes.values())
    widths = " ".join(str(progs[ch][1]) if ch else "0"
                      for ch in [next((c for c in codes if codes[c] == i), "") for i in range(first, last + 1)])
    enc = add(f"<< /Type /Encoding /Differences [{diffs}] >>")
    cps = add("<< " + " ".join(f"/{names[ch]} {procs[ch]} 0 R" for ch in codes) + " >>")
    font = add(f"<< /Type /Font /Subtype /Type3 /FontBBox [0 -{EM} {EM * 2} {EM * 2}] "
               f"/FontMatrix [{1 / EM:.6f} 0 0 {1 / EM:.6f} 0 0] /CharProcs {cps} 0 R /Encoding {enc} 0 R "
               f"/FirstChar {first} /LastChar {last} /Widths [{widths}] /Resources << >> >>")
    page_ids = []
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    contents = []
    for chunk in chunks:
        ops = ["BT", f"/F1 {SIZE} Tf"]
        y = 800
        for ln in chunk:
            hexs = "".join(f"{codes[ch]:02x}" for ch in ln)
            ops.append(f"1 0 0 1 40 {y} Tm <{hexs}> Tj")
            y -= LEAD
        ops.append("ET")
        contents.append(stream("\n".join(ops).encode()))
    pages_id = len(objs) + len(chunks) + 1
    for c in contents:
        page_ids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] /Contents {c} 0 R "
                            f"/Resources << /Font << /F1 {font} 0 R >> >> >>"))
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>") \
        == pages_id
    root = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def shapes(chars: str, font_path: str | None = None) -> dict[str, str]:
    """Karakter → glif kimliği (app/glyphs.py shape_id) — eşlemeyi elle tohumlamak için."""
    from app.glyphs import shape_id

    return {ch: shape_id(glyph_program(ch, font_path)[0]) for ch in set(chars) if ch != " "}
