"""iOS "HesapKitap'a gönder" kısayolunu üret ve imzala (macOS gerekir: `shortcuts sign`).

Kullanım:  python3 scripts/make_shortcut.py https://sunucun.example.com  [çıktı.shortcut]
Oluşan dosyayı sunucuda data/shortcuts/hesapkitap.shortcut olarak koy; Ayarlar → Telefondan paylaş'taki
"Kısayolu ekle" düğmesi onu verir. Eklerken iPhone anahtarı (ve istenirse sunucu adresini) sorar.
"""
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


def build(base_url: str) -> dict:
    key_uuid, req_uuid = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
    share_url = base_url.rstrip("/") + "/api/share"
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
         "WFWorkflowActionParameters": {"UUID": key_uuid, "WFTextActionText": "ANAHTARI_YAPISTIR"}},
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
                  "WFValue": {"Value": {"Value": {"Type": "ExtensionInput"}, "WFSerializationType": "WFTextTokenAttachment"},
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
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowInputContentItemClasses": ["WFPDFContentItem", "WFImageContentItem", "WFGenericFileContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowImportQuestions": [
            {"ActionIndex": 0, "Category": "Parameter", "ParameterKey": "WFTextActionText", "DefaultValue": "",
             "Text": "HesapKitap anahtarını yapıştır (Ayarlar → Telefondan paylaş → Anahtar oluştur)"},
            {"ActionIndex": 1, "Category": "Parameter", "ParameterKey": "WFURL", "DefaultValue": share_url,
             "Text": "Sunucu adresi (değiştirme)"},
        ],
        "WFWorkflowActions": actions,
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dest = sys.argv[2] if len(sys.argv) > 2 else "hesapkitap.shortcut"
    with tempfile.NamedTemporaryFile(suffix=".shortcut", delete=False) as f:
        plistlib.dump(build(sys.argv[1]), f, fmt=plistlib.FMT_BINARY)
        raw = f.name
    subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", raw, "--output", dest], check=True)
    print(f"İmzalandı: {dest}")


if __name__ == "__main__":
    main()
