#!/bin/bash
# =============================================================================
#  Plashker — сборка Mac-приложения и DMG одной командой.
#  Запускать НА MAC, из корня проекта:
#       chmod +x build-mac.sh      # один раз, чтобы файл стал исполняемым
#       ./build-mac.sh
#  На выходе: Plashker-Installer.dmg — готовый образ для установки на любой Mac.
# =============================================================================
set -e
cd "$(dirname "$0")"

APP="dist/Plashker.app"
DMG="Plashker-Installer.dmg"
BG="plashker_dmg_background_1320x800.png"

echo "==> 1/4  Готовлю виртуальное окружение и зависимости…"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
pip install pyinstaller >/dev/null

echo "==> 2/4  Собираю Plashker.app (PyInstaller)…"
rm -rf build dist
pyinstaller Plashker-mac.spec --noconfirm

if [ ! -d "$APP" ]; then
  echo "ОШИБКА: $APP не собрался. Смотри вывод PyInstaller выше."
  exit 1
fi

echo "==> 3/4  Делаю DMG…"
rm -f "$DMG"

if command -v create-dmg >/dev/null 2>&1; then
  # Красивый DMG с фоном и стрелкой на «Программы».
  create-dmg \
    --volname "Plashker" \
    --background "$BG" \
    --window-pos 200 120 \
    --window-size 660 400 \
    --icon-size 120 \
    --icon "Plashker.app" 175 190 \
    --app-drop-link 485 190 \
    --hide-extension "Plashker.app" \
    --no-internet-enable \
    "$DMG" \
    "$APP"
else
  echo "    (create-dmg не установлен — делаю простой DMG через hdiutil.)"
  echo "    Для красивого DMG с фоном: brew install create-dmg, затем запусти скрипт снова."
  STAGE="dmg-staging"
  rm -rf "$STAGE"; mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE"/
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "Plashker" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
  rm -rf "$STAGE"
fi

echo "==> 4/4  Готово!"
echo "    Установщик: $(pwd)/$DMG"
echo "    Отдай этот .dmg кому угодно с Mac — открывают, перетаскивают Plashker в «Программы»."
