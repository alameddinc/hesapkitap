// İstemcide rakam toplama kuralı (denetim C7, Ek C §5.4): para rakamı sunucuda hesaplanır, web yalnız gösterir.
// web/src altındaki her `.reduce(` ../../parity/reduce-allowlist.json'daki dosya sayısıyla karşılaştırılır. Satırında
// "parity-ok:" yazan reduce sayılmaz (SUNUM / GİRDİ). Basit metin taraması: for döngüsüyle toplamayı yakalamaz.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL(".", import.meta.url));
const ROOT = join(SRC, "..", "..");
const ALLOW = JSON.parse(readFileSync(join(ROOT, "parity", "reduce-allowlist.json"), "utf8")) as
  Record<string, { count: number; etiket: string } | string[]>;

function* files(dir: string): Generator<string> {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* files(p);
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) yield p;
  }
}

/** dosya (depo köküne göre) → izinsiz .reduce( sayısı */
export function reduceCounts(): Map<string, number> {
  const out = new Map<string, number>();
  for (const f of files(SRC)) {
    const n = readFileSync(f, "utf8").split("\n")
      .filter((l) => l.includes(".reduce(") && !l.includes("parity-ok:")).length;
    if (n) out.set(relative(ROOT, f).split("\\").join("/"), n);
  }
  return out;
}

test("web'de yeni .reduce( yok (izin listesiyle aynı)", () => {
  const found = reduceCounts();
  const errors: string[] = [];
  for (const [file, n] of found) {
    const a = ALLOW[file];
    const allowed = a && !Array.isArray(a) ? a.count : 0;
    if (n > allowed) {
      errors.push(`${file}: ${n} .reduce( (izinli ${allowed}). Rakamı sunucudan al; para değilse satıra "// parity-ok: SUNUM" ya da "GİRDİ" yaz.`);
    } else if (n < allowed) {
      errors.push(`${file}: ${n} .reduce( kaldı, izin listesinde ${allowed}: parity/reduce-allowlist.json'da sayıyı düşür.`);
    }
  }
  for (const [file, a] of Object.entries(ALLOW)) {
    if (!file.startsWith("_") && !Array.isArray(a) && !found.has(file)) {
      errors.push(`${file}: artık .reduce( yok; parity/reduce-allowlist.json'dan çıkar.`);
    }
  }
  assert.deepEqual(errors, []);
});

test("izin listesindeki her kaydın etiketi var", () => {
  for (const [file, a] of Object.entries(ALLOW)) {
    if (file.startsWith("_") || Array.isArray(a)) continue;
    assert.ok(["SUNUM", "GİRDİ", "YEDEK", "TAŞI"].includes(a.etiket), `${file}: etiket ${a.etiket}`);
  }
});
