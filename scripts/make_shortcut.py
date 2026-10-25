"""iOS kısayollarını üret ve imzala (macOS gerekir: `shortcuts sign`).

Kullanım:  python3 scripts/make_shortcut.py https://sunucun.example.com  [klasör]
İki dosya üretir; sunucuda data/shortcuts/ altına koy, Ayarlar → Telefondan paylaş'taki düğmeler onları verir:
  hesapkitap.shortcut      "HesapKitap'a gönder": Paylaş menüsünden PDF/fotoğraf gönderir
  hesapkitap-fis.shortcut  "Fiş çek": kamerayı açar, çekilen fişi gönderir (ana ekran / kilit ekranı widget'ı,
                           Eylem düğmesi ya da Arkaya dokun'a bağlanır)
Eklerken iPhone anahtarı (ve istenirse sunucu adresini) sorar.
"""
import os
import plistlib
import subprocess
import sys
import tempfile
import uuid

OBJ = "￼"  # metin içindeki değişken yer tutucusu


def text(s: str, attachments: dict | None = None) -> dict:
    return {"Value": {"string": s, "attachmentsByRange": attachments or {}}, "WFSerializationType": "WFTextTokenString"}


def out(uid: str, name: str) -> dict:
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}


def build(base_url: str, camera: bool = False) -> dict:
    key_uuid, req_uuid, photo_uuid = (str(uuid.uuid4()).upper() for _ in range(3))
    share_url = base_url.rstrip("/") + "/api/share"
    # gönderilecek dosya: paylaşılan girdi ya da kameradan çekilen fotoğraf
    file_value = {"Type": "ActionOutput", "OutputUUID": photo_uuid, "OutputName": "Fotoğraf"} if camera \
        else {"Type": "ExtensionInput"}
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
         "WFWorkflowActionParameters": {"UUID": key_uuid, "WFTextActionText": "ANAHTARI_YAPISTIR"}},
        *([{"WFWorkflowActionIdentifier": "is.workflow.actions.takephoto",
            "WFWorkflowActionParameters": {"UUID": photo_uuid, "WFCameraCaptureShowPreview": True,
                                           "WFCameraCaptureDevice": "Back", "WFPhotoCount": 1}}] if camera else []),
        {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
         "WFWorkflowActionParameters": {
             "UUID": req_uuid, "WFURL": share_url, "WFHTTPMethod": "POST", "ShowHeaders": True,
             "WFHTTPHeaders": {"Value": {"WFDictionaryFieldValueItems": [
                 {"WFItemType": 0, "WFKey": text("Authorization"),
                  "WFValue": text("Bearer " + OBJ, {"{7, 1}": out(key_uuid, "Metin")})},
                 {"WFItemType": 0, "WFKey": text("Accept"), "WFValue": text("text/plain")},
             ]}, "WFSerializationType": "WFDictionaryFieldValue"},
             "WFHTTPBodyType": "Form",
             "WFFormValues": {"Value": {"WFDictionaryFieldValueItems": [
                 {"WFItemType": 5, "WFKey": text("file"),
                  "WFValue": {"Value": {"Value": file_value, "WFSerializationType": "WFTextTokenAttachment"},
                              "WFSerializationType": "WFTokenAttachmentParameterState"}},
             ]}, "WFSerializationType": "WFDictionaryFieldValue"},
         }},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.notification",
         "WFWorkflowActionParameters": {"WFNotificationActionTitle": "HesapKitap",
                                        "WFNotificationActionBody": text(OBJ, {"{0, 1}": out(req_uuid, "URL'nin İçeriği")})}},
    ]
    return {
        "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowClientVersion": "2302.0.4",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983 if camera else 463140863,
                           "WFWorkflowIconGlyphNumber": 59784 if camera else 59511},  # kamera / gönder simgesi
        "WFWorkflowTypes": [] if camera else ["ActionExtension"],
        "WFWorkflowHasShortcutInputVariables": not camera,
        "WFWorkflowInputContentItemClasses": [] if camera else ["WFPDFContentItem", "WFImageContentItem", "WFGenericFileContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowImportQuestions": [
            {"ActionIndex": 0, "Category": "Parameter", "ParameterKey": "WFTextActionText", "DefaultValue": "",
             "Text": "HesapKitap anahtarını yapıştır (Ayarlar → Telefondan paylaş → Anahtar oluştur)"},
            {"ActionIndex": 2 if camera else 1, "Category": "Parameter", "ParameterKey": "WFURL", "DefaultValue": share_url,
             "Text": "Sunucu adresi (değiştirme)"},
        ],
        "WFWorkflowActions": actions,
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    folder = sys.argv[2] if len(sys.argv) > 2 else "."
    for name, camera in (("hesapkitap.shortcut", False), ("hesapkitap-fis.shortcut", True)):
        dest = os.path.join(folder, name)
        with tempfile.NamedTemporaryFile(suffix=".shortcut", delete=False) as f:
            plistlib.dump(build(sys.argv[1], camera), f, fmt=plistlib.FMT_BINARY)
            raw = f.name
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", raw, "--output", dest], check=True)
        print(f"İmzalandı: {dest}")


if __name__ == "__main__":
    main()
