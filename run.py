#!/usr/bin/env python3
"""
Plashker — точка входа. Запускает окно pywebview с UI на HTML/CSS/JS и
мостом к Python-движку компоновки.

  python run.py

Требуется pywebview (см. requirements.txt): pip install pywebview
На Linux может понадобиться системный webview-бэкенд (GTK/Qt) — см. README.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

UI_INDEX = os.path.join(HERE, "ui", "index.html")


def main() -> None:
    try:
        import webview
    except ImportError:
        sys.exit(
            "pywebview не установлен. Установите его:\n"
            "    python -m venv .venv && source .venv/bin/activate\n"
            "    pip install -r requirements.txt\n"
        )

    from plashker.app import Api

    api = Api()
    window = webview.create_window(
        "Plashker",
        UI_INDEX,
        js_api=api,
        width=1408,
        height=946,
        min_size=(1144, 792),
        background_color="#FBF3E3",
    )
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
