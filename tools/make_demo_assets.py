"""
Plashker — генератор демо-ассетов и конфигов.

Запуск:  python -m tools.make_demo_assets

Создаёт всё, что нужно, чтобы приложение запускалось «из коробки»:
  data/safe_zones/*.png      — встроенные рамки safe zone (залиты, дырка в центре)
  data/global_assets/*.png   — РЕКЛАМА / наша юр.инфо / совмещённый / фон
  data/formats_template.json — шаблон форматов с авто-вычисленным frame_rect_pct
  data/app_settings.json     — конфиг глобальных ассетов
  examples/MICHAEL/          — демо-проект (title/date/rating + manifest.json)

Ассеты намеренно делаются с прозрачными полями по краям, чтобы проверялась
не деструктивная авто-обрезка (content_bbox), и safe-zone — инверсией альфы.
"""

from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SAFE = os.path.join(DATA, "safe_zones")
GLOB = os.path.join(DATA, "global_assets")
EX = os.path.join(ROOT, "examples", "MICHAEL")
ASSETS = os.path.join(EX, "assets")

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

INK = (90, 58, 24, 255)        # тёплые чернила #5A3A18
MARIGOLD = (242, 165, 60, 255)
CHERRY = (200, 69, 60, 255)
CREAM = (251, 243, 227, 255)

# (key, family, orientation, size_px, margin%, legal_mirrored, supports_regions, title_mirrored)
FORMATS = [
    ("16x9",          "social", "horizontal", (1920, 1080), (0.02, 0.03), False, True,  True),
    ("9x16",          "social", "vertical",   (1080, 1920), (0.04, 0.04), False, True,  False),
    ("4x5",           "social", "vertical",   (1080, 1350), (0.03, 0.03), False, True,  True),
    ("1x1",           "social", "horizontal", (1080, 1080), (0.03, 0.03), False, True,  True),
    ("byyd_1024x768", "byyd",   "horizontal", (1024, 768),  (0.03, 0.03), False, False, True),
    ("byyd_768x1024", "byyd",   "vertical",   (768, 1024),  (0.03, 0.03), False, False, True),
    ("byyd_480x320",  "byyd",   "horizontal", (480, 320),   (0.04, 0.04), False, False, True),
    ("byyd_320x480",  "byyd",   "vertical",   (320, 480),   (0.04, 0.04), True,  False, True),
    ("da_1280x720",   "da",     "horizontal", (1280, 720),  (0.03, 0.03), False, True,  False),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD, size)


def text_png(text: str, *, fill, fontsize: int, pad: int = 40,
             bg=None) -> Image.Image:
    """Нарисовать текст на прозрачном фоне с прозрачными полями (pad).

    Поля специально оставлены прозрачными — это проверка авто-обрезки.
    """
    f = font(fontsize)
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    box = d.textbbox((0, 0), text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    W, H = tw + pad * 2, th + pad * 2
    img = Image.new("RGBA", (W, H), bg or (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - box[0], pad - box[1]), text, font=f, fill=fill)
    return img


def make_safe_zone(size, margin) -> Image.Image:
    """Рамка safe zone: залита полупрозрачным, прозрачная «дырка» в центре."""
    W, H = size
    mx, my = margin
    img = Image.new("RGBA", (W, H), (90, 58, 24, 120))  # залитое поле
    left = round(W * mx)
    top = round(H * my)
    right = W - left
    bottom = H - top
    # прозрачная дырка = сама безопасная зона
    hole = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    img.paste(hole, (left, top))
    return img


def make_global_assets() -> None:
    os.makedirs(GLOB, exist_ok=True)
    # горизонтальные: РЕКЛАМА + наша юр.инфо отдельными файлами — одинаковый размер (п.3 v0.5.2)
    text_png("РЕКЛАМА", fill=INK, fontsize=64).save(
        os.path.join(GLOB, "ad_label_h.png"))
    text_png("ООО «КИНОПРОКАТ» · ОГРН 0000000000000",
             fill=INK, fontsize=64).save(os.path.join(GLOB, "our_legal_h.png"))
    # вертикальный: совмещённый файл (повёрнут к краю)
    combined = text_png("РЕКЛАМА · ООО «КИНОПРОКАТ»", fill=INK, fontsize=44)
    combined.rotate(90, expand=True).save(
        os.path.join(GLOB, "ad_legal_combined_v.png"))
    # декоративный фон превью
    bg = Image.new("RGBA", (1920, 1080))
    dr = ImageDraw.Draw(bg)
    for y in range(1080):
        t = y / 1080
        r = int(43 + t * 30); g = int(33 + t * 20); b = int(24 + t * 12)
        dr.line([(0, y), (1920, y)], fill=(r, g, b, 255))
    bg.save(os.path.join(GLOB, "preview_bg.png"))


def make_safe_zones() -> dict:
    os.makedirs(SAFE, exist_ok=True)
    # импортируем функцию инверсии альфы из самого движка — единый источник истины
    import sys
    sys.path.insert(0, ROOT)
    from plashker.engine.assets import compute_safe_zone_rect

    template = {}
    for key, family, orient, size, margin, mirrored, supports, title_mir in FORMATS:
        path = os.path.join(SAFE, f"{key}.png")
        make_safe_zone(size, margin).save(path)
        rect = compute_safe_zone_rect(path)
        entry = {
            "family": family,
            "orientation": orient,
            "size_px": list(size),
            "safe_zone": {
                "source_file": f"safe_zones/{key}.png",
                "frame_rect_pct": [round(v, 4) for v in rect],
            },
        }
        if mirrored:
            entry["legal_mirrored"] = True
        if title_mir:
            entry["title_mirrored"] = True
        if not supports:
            entry["supports_regions"] = False
        template[key] = entry
    return template


def _make_kz_rating_diamond() -> Image.Image:
    """Казахский ВО: цифра в ромбе (diamond). п.7 v0.5."""
    text = "16+"
    fs = 160
    f = font(fs)
    pad = 80
    # измеряем текст
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    box = d.textbbox((0, 0), text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    # ромб — повёрнутый на 45° квадрат, сторона = диагональ вмещающего квадрата
    inner = max(tw, th) + 40      # с запасом внутри ромба
    diag = int(inner * 1.42) + 2  # диагональ ≈ сторона * √2
    W = H = diag + pad * 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    half = diag // 2
    diamond = [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
    d.polygon(diamond, outline=CHERRY, width=8)
    # текст в центре ромба
    d.text((cx - tw // 2 - box[0], cy - th // 2 - box[1]), text, font=f, fill=CHERRY)
    return img


def make_demo_project() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    import sys
    sys.path.insert(0, ROOT)
    from plashker.engine.assets import content_bbox_pct

    files = {
        "title.png":        ("MICHAEL", MARIGOLD, 220),
        "date_ru_date.png": ("27 ИЮНЯ", CREAM, 150),
        "date_ru_now.png":  ("УЖЕ В КИНО", CREAM, 150),
        "rating_ru.png":    ("16+", CHERRY, 200),
        "date_kz_date.png": ("27 МАУСЫМ\n27 ИЮНЯ", CREAM, 120),
        "legal_byyd_1024x768.png": ("Прокатное удостоверение №000",
                                    INK, 36),
    }
    for name, (text, fill, fs) in files.items():
        text_png(text, fill=fill, fontsize=fs).save(os.path.join(ASSETS, name))

    # п.7 v0.5: KZ рейтинг в ромбике (казахский дизайн ВО)
    _make_kz_rating_diamond().save(os.path.join(ASSETS, "rating_kz.png"))

    def el(rel, axis):
        p = os.path.join(ASSETS, os.path.basename(rel))
        return {
            "file": rel,
            "has_alpha": True,
            "anchor_axis": axis,
            "content_bbox_pct": [round(v, 4) for v in content_bbox_pct(p)],
        }

    manifest = {
        "version": 1,
        "project_meta": {
            "movie_title": "MICHAEL",
            "project_file_path": "examples/MICHAEL/MICHAEL.plshk",
            "created_at": "2026-06-27T12:00:00Z",
            "modified_at": "2026-06-27T12:00:00Z",
        },
        "elements": {
            "title": el("assets/title.png", "width"),
            "by_region": {
                "RU": {
                    "date_variants": {
                        "active": "date",
                        "items": {
                            "date": el("assets/date_ru_date.png", "width"),
                            "now": el("assets/date_ru_now.png", "width"),
                        },
                    },
                    "rating": el("assets/rating_ru.png", "height"),
                },
                "KZ": {
                    "date_variants": {
                        "active": "date",
                        "items": {
                            "date": el("assets/date_kz_date.png", "width"),
                        },
                    },
                    "rating": el("assets/rating_kz.png", "height"),
                },
                "BY": {"date_variants": None, "rating": None},
            },
        },
        "active_region": "RU",
        "region_visibility": {"RU": True, "KZ": True, "BY": False},
        "formats": _demo_formats(),
        "export_templates": [
            {
                "name": "Соцсети только PNG",
                "formats_include": ["16x9", "9x16", "4x5", "1x1"],
                "export_png": True,
                "export_psd": False,
                "export_jpeg_for_approval": False,
                "strip_legal": False,
            }
        ],
    }
    with open(os.path.join(EX, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# дефолты scale из таблицы спеки (раздел «Что собирать в таблицу»)
DEFAULTS = {
    "16x9":          (16.56, 10.10, 7.41, 1.48),
    "4x5":           (30.37, 18.52, 6.22, 1.19),
    "1x1":           (25.37, 15.56, 6.48, 1.11),
    "9x16":          (35.65, 21.85, 4.90, 1.09),
    "byyd_320x480":  (41.56, 31.25, 5.83, 1.04),
    "byyd_480x320":  (27.71, 24.58, 10.00, 2.50),
    "byyd_768x1024": (37.24, 25.65, 5.86, 1.66),
    "byyd_1024x768": (24.12, 23.54, 7.16, 1.56),
    "da_1280x720":   (20.00, 12.00, 8.00, 1.50),
}


def _demo_formats() -> dict:
    out = {}
    for key, family, orient, size, *_ in FORMATS:
        t, d, r, g = DEFAULTS.get(key, (20.0, 12.0, 8.0, 1.5))
        legal = {
            "show_platform_legal": False,
            "platform_legal_file": None,
            "gap_legal_pct": 1.5,
        }
        if orient == "vertical":
            legal["show_ad_and_legal_combined"] = True
        else:
            legal["show_ad_label"] = True
            legal["show_our_legal"] = True
        out[key] = {
            "region": "RU",
            "visible": True,
            "linked": True,
            "settings": {
                "title_scale_pct": t,
                "date_scale_pct": d,
                "rating_scale_pct": r,
                "gap_title_date_pct": g,
            },
            "legal": legal,
        }
    return out


def make_app_settings() -> None:
    settings = {
        "horizontal": {
            "ad_label": {"file": "global_assets/ad_label_h.png"},
            "our_legal": {"file": "global_assets/our_legal_h.png"},
        },
        "vertical": {
            "ad_and_legal_combined": {"file": "global_assets/ad_legal_combined_v.png"},
        },
        "preview_background": {
            "file": "global_assets/preview_bg.png",
            "fit_mode": "cover",
        },
    }
    with open(os.path.join(DATA, "app_settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    print("· global assets")
    make_global_assets()
    print("· safe zones (inverted-alpha bbox)")
    template = make_safe_zones()
    with open(os.path.join(DATA, "formats_template.json"), "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    print("· app settings")
    make_app_settings()
    print("· demo project MICHAEL")
    make_demo_project()
    print("done.")


if __name__ == "__main__":
    main()
