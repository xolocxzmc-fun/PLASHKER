# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для Windows 10 / 11.
# Запускать ИЗ КОРНЯ проекта (там, где run.py), положив рядом
# AppIcon.ico и DocIcon.ico:
#     pyinstaller Plashker-windows.spec
#
# Отличия от macOS-версии:
#   - webview на Windows использует бэкенд EdgeChromium (WebView2), не cocoa
#   - иконка в формате .ico, а не .icns
#   - на выходе получаем папку dist\Plashker\ с Plashker.exe (не .app / не .dmg)

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui", "ui"),
        ("data", "data"),
        ("examples", "examples"),
        ("DocIcon.ico", "."),
    ],
    hiddenimports=[
        "webview.platforms.edgechromium",
    ],
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
    upx=False,          # UPX выключен: он часто ломает WebView2-библиотеки
    console=False,      # без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="AppIcon.ico",
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
