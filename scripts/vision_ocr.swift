// Fiş fotoğraflarından metin (macOS, Apple Vision) — ölçüm aracının (app/receipt_bench.py --text-dir) girdisi.
//
//     swift scripts/vision_ocr.swift ~/Projects/hk-fis-test            (metinler fotoğrafların yanına)
//     swift scripts/vision_ocr.swift FOTO_KLASORU METIN_KLASORU         (metinler ayrı klasöre)
//     swift scripts/vision_ocr.swift fis.jpg                            (tek dosya: metin standart çıktıya)
//
// Her fotoğraf (jpg, jpeg, png, heic) için <ad uzantısız>.txt yazılır (varsa üzerine). Telefondaki okumayla aynı:
// VNRecognizeTextRequest accurate, dil "tr-TR" (bu sistemde destekleniyorsa; yoksa "en-US", Latin harfler yine okunur),
// sonra satır birleştirme: eğim düzeltilir, dikey olarak örtüşen kutular (ürün adı ve sağdaki fiyatı) tek satırda,
// soldan sağa birleşir. Bu birleştirme mobildeki src/lib/metinOku.ts `satirlariBirlestir` ile aynı kuraldır.
//
// Kişisel veri: klasörler repo DIŞINDA olmalı; betik repo içine yazmayı reddeder. Hiçbir şey ağa gönderilmez.
import Foundation
import ImageIO
import Vision

struct Kutu { let text: String; let x: Double; let y: Double; let w: Double; let h: Double; let egim: Double }

struct K { let k: Kutu; let cy: Double }
func ort(_ s: [K]) -> Double { s.map { $0.cy }.reduce(0, +) / Double(s.count) }

/// Mobildeki grupla ile aynı: eğim düzeltilmiş dikey merkezler yarım kutu boyundan yakınsa ve yatayda üst üste
/// binmiyorlarsa (binen iki kutu ayrı satırdır) aynı satır. Son üç satıra bakılır.
func grupla(_ kutular: [Kutu], _ egim: Double) -> [[K]] {
    let sirali = kutular.map { K(k: $0, cy: $0.y + $0.h / 2 - egim * ($0.x + $0.w / 2)) }.sorted { $0.cy < $1.cy }
    var satirlar: [[K]] = []
    for b in sirali {
        var yer: Int? = nil
        if !satirlar.isEmpty {
            for i in stride(from: satirlar.count - 1, through: max(0, satirlar.count - 3), by: -1) {
                let s = satirlar[i]
                let h = s.map { $0.k.h }.max() ?? b.k.h
                let yakin = abs(b.cy - ort(s)) <= 0.5 * min(b.k.h, h)
                let carpisma = s.contains { min($0.k.x + $0.k.w, b.k.x + b.k.w) - max($0.k.x, b.k.x) > 0.3 * min($0.k.w, b.k.w) }
                if yakin && !carpisma { yer = i; break }
            }
        }
        if let i = yer { satirlar[i].append(b) } else { satirlar.append([b]) }
    }
    return satirlar
}

/// Mobildeki egimBul ile aynı: −0,15…0,15 (0,0025 adım) arasında en az satır veren eğim; eşitlikte geniş kutuların
/// kendi eğimlerinin ortancasına en yakını.
func egimBul(_ kutular: [Kutu]) -> Double {
    let genis = kutular.filter { $0.w > 3 * $0.h && $0.egim.isFinite }.map { $0.egim }.sorted()
    let onsel = genis.isEmpty ? 0 : max(-0.15, min(0.15, genis[genis.count / 2]))
    var best = (n: Int.max, d: Double.infinity, e: 0.0)
    for i in -60...60 {
        let e = Double(i) * 0.0025
        let n = grupla(kutular, e).count
        let d = abs(e - onsel)
        if n < best.n || (n == best.n && d < best.d) { best = (n, d, e) }
    }
    return best.e
}

/// Mobildeki satirlariBirlestir (src/lib/metinOku.ts) ile aynı: piksel koordinatı (sol üst köken) kutulardan satır
/// satır metin; aynı satırdakiler soldan sağa, geniş aralıkta üç boşluk.
func satirlariBirlestir(_ girdi: [Kutu]) -> String {
    let kutular = girdi.filter { !$0.text.trimmingCharacters(in: .whitespaces).isEmpty && $0.h > 0 && $0.w >= 0 }
    if kutular.isEmpty { return "" }
    return grupla(kutular, egimBul(kutular)).sorted { ort($0) < ort($1) }.map { s -> String in
        let ss = s.sorted { $0.k.x < $1.k.x }
        var out = ss[0].k.text.trimmingCharacters(in: .whitespaces)
        for j in stride(from: 1, to: ss.count, by: 1) {
            let o = ss[j - 1].k, k = ss[j].k
            out += (k.x - (o.x + o.w) > 1.5 * max(o.h, k.h) ? "   " : " ") + k.text.trimmingCharacters(in: .whitespaces)
        }
        return out
    }.joined(separator: "\n")
}

func diller() -> [String] {
    let r = VNRecognizeTextRequest()
    r.recognitionLevel = .accurate
    let destek = (try? r.supportedRecognitionLanguages()) ?? []
    return destek.contains("tr-TR") ? ["tr-TR", "en-US"] : ["en-US"]
}

func oku(_ url: URL) throws -> String {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any] else {
        throw NSError(domain: "vision_ocr", code: 1, userInfo: [NSLocalizedDescriptionKey: "açılamadı"])
    }
    let yon = (props[kCGImagePropertyOrientation] as? UInt32).flatMap { CGImagePropertyOrientation(rawValue: $0) } ?? .up
    var W = (props[kCGImagePropertyPixelWidth] as? Double) ?? 1, H = (props[kCGImagePropertyPixelHeight] as? Double) ?? 1
    if [.left, .right, .leftMirrored, .rightMirrored].contains(yon) { swap(&W, &H) }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = diller()
    req.usesLanguageCorrection = true
    try VNImageRequestHandler(url: url, orientation: yon, options: [:]).perform([req])
    let kutular = (req.results ?? []).compactMap { o -> Kutu? in
        guard let t = o.topCandidates(1).first?.string else { return nil }
        let bb = o.boundingBox  // 0–1, sol alt köken
        let dx = (o.topRight.x - o.topLeft.x) * W, dy = -(o.topRight.y - o.topLeft.y) * H
        return Kutu(text: t, x: bb.minX * W, y: (1 - bb.maxY) * H, w: bb.width * W, h: bb.height * H,
                    egim: abs(dx) > 0 ? dy / dx : 0)
    }
    return satirlariBirlestir(kutular)
}

let args = Array(CommandLine.arguments.dropFirst())
let fm = FileManager.default
guard let ilk = args.first else {
    FileHandle.standardError.write("kullanım: swift scripts/vision_ocr.swift FOTO_KLASORU METIN_KLASORU | fis.jpg\n".data(using: .utf8)!)
    exit(2)
}
var klasor: ObjCBool = false
if fm.fileExists(atPath: ilk, isDirectory: &klasor), !klasor.boolValue {
    print(try oku(URL(fileURLWithPath: ilk)))
    exit(0)
}
guard klasor.boolValue, args.count <= 2 else {
    FileHandle.standardError.write("kullanım: swift scripts/vision_ocr.swift FOTO_KLASORU [METIN_KLASORU] | fis.jpg\n".data(using: .utf8)!)
    exit(2)
}
let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().standardizedFileURL.path
let hedef = URL(fileURLWithPath: args.count == 2 ? args[1] : ilk).standardizedFileURL
if hedef.path == repo || hedef.path.hasPrefix(repo + "/") {
    FileHandle.standardError.write("metinler repo dışındaki bir klasöre yazılmalı (kişisel veri)\n".data(using: .utf8)!)
    exit(2)
}
try fm.createDirectory(at: hedef, withIntermediateDirectories: true)
let uzantilar: Set<String> = ["jpg", "jpeg", "png", "heic"]
let dosyalar = try fm.contentsOfDirectory(atPath: ilk).filter { uzantilar.contains(($0 as NSString).pathExtension.lowercased()) }.sorted()
FileHandle.standardError.write("dil: \(diller().joined(separator: ", "))\n".data(using: .utf8)!)
var n = 0, hata = 0
for d in dosyalar {
    do {
        let metin = try oku(URL(fileURLWithPath: ilk).appendingPathComponent(d))
        try metin.write(to: hedef.appendingPathComponent((d as NSString).deletingPathExtension + ".txt"), atomically: true, encoding: .utf8)
        n += 1
    } catch {
        hata += 1  // dosya adı yazdırılmaz
    }
}
FileHandle.standardError.write("\(n) metin yazıldı, \(hata) hata\n".data(using: .utf8)!)
