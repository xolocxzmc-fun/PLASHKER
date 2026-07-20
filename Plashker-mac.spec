# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для macOS. Собирает Plashker.app (готовый бандл приложения).
# Запускать ИЗ КОРНЯ проекта (там, где run.py), рядом должны лежать
# AppIcon.icns и DocIcon.icns:
#     pyinstaller Plashker-mac.spec --noconfirm
#
# Отличия от Windows-версии:
#   - webview на macOS использует бэкенд cocoa (WebKit), не EdgeChromium
#   - иконки в формате .icns, а не .ico
#   - на выходе получаем dist/Plashker.app (готовый .app, из него делаем .dmg)

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui", "ui"),
        ("data", "data"),
        ("examples", "examples"),
        ("DocIcon.icns", "."),
    ],
    hiddenimports=["webview.platforms.cocoa"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Plashker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX выключен: на macOS он часто ломает подпись/загрузку .dylib
    console=False,      # без окна терминала
    disable_windowed_traceback=False,
    argv_emulation=True,   # чтобы Finder мог передать .plshk двойным кликом
    target_arch=None,      # сборка под архитектуру текущего Mac (Apple Silicon или Intel)
    codesign_identity=None,
    entitlements_file=None,
    icon="AppIcon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Plashker",
)

app = BUNDLE(
    coll,
    name="Plashker.app",
    icon="AppIcon.icns",
    bundle_identifier="com.plashker.app",
    info_plist={
        "CFBundleDisplayName": "Plashker",
        "CFBundleName": "Plashker",
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Проект Plashker",
                "CFBundleTypeRole": "Editor",
                "CFBundleTypeIconFile": "DocIcon",
                "LSItemContentTypes": ["com.plashker.project"],
                "LSHandlerRank": "Owner",
            }
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": "com.plashker.project",
                "UTTypeDescription": "Проект Plashker",
                "UTTypeIconFile": "DocIcon",
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {
                    "public.filename-extension": ["plshk"],
                },
            }
        ],
    },
)
