"""
Plashker — мост JS ↔ Python (pywebview Api).

UI на HTML/CSS/JS вызывает методы этого класса через window.pywebview.api.*.
Движок (Pillow) считает превью и отдаёт его в UI как base64-PNG, поэтому
ползунки работают «вживую»: подвинул → пересчитался текущий формат → картинка
обновилась (п.23, требование real-time на открытом формате).

Класс намеренно НЕ импортирует webview на уровне модуля — его можно
импортировать и тестировать без GUI.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import copy
from dataclasses import asdict
from typing import Optional

from PIL import Image

from .project import (
    AppConfig,
    ExportOptions,
    Project,
    export,
    _SCALE_DEFAULTS,
)
from .engine import geometry as geo
from .engine import assets
from .engine.models import KZ_DEFAULT_RATING_PX, Element

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DEMO = os.path.join(ROOT, "examples", "MICHAEL")
RECENTS_FILE = os.path.join(DATA, "recent_projects.json")

PREVIEW_MAX = 1600  # длинная сторона чёткого превью, px (п.4)
LIVE_MAX = 1200     # длинная сторона во время перетаскивания ползунка (скорость)

FAMILY_TITLES = {"social": "Соцсети", "byyd": "BYYD", "da": "Digital Alliance"}


def _png_b64(img: Image.Image, max_side: int = PREVIEW_MAX,
             fast: bool = False) -> str:
    """Сжать под превью и вернуть data-URL base64 PNG.

    fast=True — для live-перетаскивания: меньший размер и быстрый фильтр,
    чтобы превью обновлялось плавно. Чёткий кадр (LANCZOS, 1600) рендерится
    при отпускании ползунка / переключении формата.
    """
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        flt = Image.BILINEAR if fast else Image.LANCZOS
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), flt)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Api:
    """Публичный API для фронтенда."""

    def __init__(self) -> None:
        self.app = AppConfig.load(DATA)
        self._preload_safe_zone_images()
        self.project: Optional[Project] = None
        self.current_format: Optional[str] = None
        self.current_region = "RU"
        self._undo: list[dict] = []          # сессионный undo (п.11)
        self._redo: list[dict] = []          # сессионный redo
        self._edit_baselines: dict[tuple, dict] = {}  # live-drag baselines for undo
        self._zoom: dict[str, int] = {}      # сессионный зум на формат (п.16)
        self._window = None                  # ссылка на окно pywebview


    def _preload_safe_zone_images(self) -> None:
        """Прогреть кэш PNG safe-zone, чтобы первое включение оверлея не тормозило."""
        for fmt in self.app.formats.values():
            rel = fmt.safe_zone.source_file
            if not rel:
                continue
            path = rel if os.path.isabs(rel) else os.path.join(DATA, rel)
            try:
                if os.path.exists(path):
                    assets.load_rgba(path)
            except Exception:
                pass

    def set_window(self, window) -> None:
        self._window = window

    # --- нативные диалоги файлов (через pywebview) -------------------------

    def open_project_dialog(self) -> Optional[dict]:
        if not self._window:
            return None
        import webview
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Plashker (*.plshk)",))
        if not res:
            return None
        return self.open_project(res[0])

    def pick_export_dir(self) -> Optional[str]:
        if not self._window:
            return None
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None

    # --- запуск / загрузка проекта ----------------------------------------

    def open_demo(self) -> dict:
        """Открыть встроенный демо-проект MICHAEL (в копии — без правки репозитория)."""
        import shutil
        import tempfile
        workdir = tempfile.mkdtemp(prefix="plashker_demo_")
        shutil.copytree(DEMO, workdir, dirs_exist_ok=True)
        self.project = Project.open_dir(workdir)
        self.project.path = None  # «Сохранить» спросит, куда (Save As)
        self.current_region = self.project.manifest.get("active_region", "RU")
        self.current_format = self.project.format_keys[0]
        self._zoom = {}
        self._undo = []
        self._redo = []
        self._edit_baselines = {}
        self._session_calibrated = False
        return self.get_state()

    def open_project(self, path: str) -> dict:
        self.project = Project.open(path)
        self.current_region = self.project.manifest.get("active_region", "RU")
        self.current_format = self.project.format_keys[0]
        self._push_recent(path)
        self._undo = []
        self._redo = []
        self._edit_baselines = {}
        self._session_calibrated = False
        return self.get_state()

    def save_project(self, path: Optional[str] = None) -> str:
        assert self.project
        target = self.project.save(path)
        self._push_recent(target)
        return target

    def save_current(self) -> Optional[str]:
        """CMD+S: сохранить по известному пути, иначе открыть диалог Save As."""
        if not self.project:
            return None
        if self.project.path:
            return self.save_project(self.project.path)
        return self.save_project_as()

    def save_project_as(self) -> Optional[str]:
        """Системный диалог сохранения .plshk (для нового проекта / Save As)."""
        if not self.project:
            return None
        if not self._window:
            target = os.path.join(os.getcwd(),
                                  f"{self.project.movie_title}.plshk")
            return self.save_project(target)
        import webview
        res = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"{self.project.movie_title}.plshk",
            file_types=("Plashker (*.plshk)",))
        if not res:
            return None
        target = res if isinstance(res, str) else res[0]
        if not target.lower().endswith(".plshk"):
            target += ".plshk"
        return self.save_project(target)

    def save_as(self) -> Optional[str]:
        """«Сохранить как…» — всегда открывает диалог, сохраняет дубликат (п.7 v0.8).
        Оригинальный проект продолжает указывать на прежний путь."""
        if not self.project:
            return None
        old_path = self.project.path          # запомнить текущий путь
        result = self.save_project_as()       # откроет диалог и сохранит
        if result and old_path:
            self.project.path = old_path      # вернуть путь — мы сохранили копию
        return result

    # --- новый проект и импорт исходных элементов (п.2) --------------------

    def new_project(self, title: str) -> dict:
        """Создать новый пустой проект. UI затем импортирует title/date/ВО."""
        title = (title or "Без названия").strip()
        self.project = Project.new(title, self.app)
        self.current_region = "RU"
        self.current_format = self.project.format_keys[0]
        self._zoom = {}
        self._undo = []
        self._redo = []
        self._edit_baselines = {}
        self._session_calibrated = False
        return {"ok": True, "movie_title": title,
                "has_title": self.project.has_title()}

    def import_element(self, kind: str, region: str, filename: str,
                       data_url: str) -> dict:
        """Принять перетащенный PNG (base64 data-URL) и привязать к проекту."""
        if not self.project:
            return {"ok": False, "error": "Нет открытого проекта"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Не удалось прочитать файл: {e}"}
        res = self.project.import_asset(kind, region or "RU", filename, raw)
        res["has_title"] = self.project.has_title()
        return res

    def enter_editor(self) -> dict:
        """Готов ли проект к открытию редактора (есть ли название)."""
        if not self.project or not self.project.has_title():
            return {"loaded": False, "error": "Сначала загрузите «Тайтл»"}
        return self.get_state()

    def import_project_background(self, filename: str, data_url: str) -> dict:
        """Импортировать пользовательский фон для превью (п.7 v0.5.1).

        Фон сохраняется внутри проекта и подменяет дефолтный коричневый.
        """
        if not self.project:
            return {"ok": False, "error": "Нет открытого проекта"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        os.makedirs(os.path.join(self.project.workdir, "assets"), exist_ok=True)
        rel = "assets/preview_background.png"
        with open(os.path.join(self.project.workdir, rel), "wb") as f:
            f.write(raw)
        assets.clear_cache()
        self.project.manifest.setdefault("project_meta", {})["preview_background"] = rel
        return {"ok": True}

    # --- недавние проекты (п.9) -------------------------------------------

    def get_recent_projects(self) -> list[dict]:
        items = _load_recents()
        # отфильтровать те, что больше не существуют на диске
        alive = [r for r in items if r.get("path") and os.path.exists(r["path"])]
        if len(alive) != len(items):
            _save_recents(alive)
        return alive

    def _push_recent(self, path: str) -> None:
        if not path:
            return
        items = [r for r in _load_recents() if r.get("path") != path]
        title = self.project.movie_title if self.project else os.path.basename(path)
        items.insert(0, {"path": path, "title": title, "ts": time.time()})
        _save_recents(items[:12])

    # --- тема оформления (п.5/6) -----------------------------------------

    def get_theme(self) -> str:
        """Текущая тема приложения: 'light' | 'dark' (из app_settings.json)."""
        try:
            sp = os.path.join(DATA, "app_settings.json")
            with open(sp, encoding="utf-8") as f:
                cfg = json.load(f)
            return "dark" if cfg.get("theme") == "dark" else "light"
        except (OSError, ValueError):
            return "light"

    def set_theme(self, theme: str) -> dict:
        """Сохранить тему глобально (для всех проектов)."""
        theme = "dark" if theme == "dark" else "light"
        sp = os.path.join(DATA, "app_settings.json")
        try:
            with open(sp, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        cfg["theme"] = theme
        try:
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            return {"ok": False, "error": "Не удалось сохранить настройку"}
        return {"ok": True, "theme": theme}

    # --- настройки приложения (глобальные ассеты, п.8) --------------------

    def get_app_assets(self) -> dict:
        g = self.app.globals_
        def slot(rel):
            return {"file": rel, "thumb": self._thumb(self.app.global_root, rel)}
        return {
            "ad_label_h": slot(g.ad_label_h),
            "our_legal_h": slot(g.our_legal_h),
            "ad_and_legal_combined_v": slot(g.ad_and_legal_combined_v),
            "preview_background": slot(g.preview_background),
            "safe_zones": {k: {"file": v.safe_zone.source_file,
                               "thumb": self._safe_zone_thumb(self.app.data_root,
                                                    v.safe_zone.source_file)}
                           for k, v in self.app.formats.items()},
        }

    def _thumb(self, root: str, rel: Optional[str], max_side: int = 220
               ) -> Optional[str]:
        if not rel:
            return None
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.exists(path):
            return None
        try:
            img = Image.open(path).convert("RGBA")
            # UI-превью нормализуем «на корню»: режем по реальному alpha-контенту
            # с порогом, добавляем воздух и кладём в квадратный прозрачный холст.
            # Так ВО/даты с большими прозрачными полями или нестандартными
            # пропорциями не уезжают в низ миниатюры и не показываются кусочком.
            a = img.getchannel("A")
            mask = a.point(lambda v: 255 if v > 12 else 0)
            bbox = mask.getbbox()
            if bbox:
                img = img.crop(bbox)
                side = max(img.size)
                pad = max(8, round(side * 0.12))
                side = side + pad * 2
                norm = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                norm.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
                img = norm
            return _png_b64(img, max_side, fast=True)
        except Exception:
            return None

    def _safe_zone_thumb(self, root: str, rel: Optional[str],
                         max_side: int = 260) -> Optional[str]:
        """Превью safe-zone как есть — без цветовых искажений (п.2 v0.9)."""
        if not rel:
            return None
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.exists(path):
            return None
        try:
            return _png_b64(Image.open(path).convert("RGBA"), max_side, fast=True)
        except Exception:
            return None

    # слот глобального ассета -> (секция, ключ) в app_settings.json + поле globals_
    _GLOBAL_SLOTS = {
        "ad_label_h":              ("horizontal", "ad_label"),
        "our_legal_h":             ("horizontal", "our_legal"),
        "ad_and_legal_combined_v": ("vertical",   "ad_and_legal_combined"),
        "preview_background":      ("preview_background", None),
    }

    def replace_global_asset(self, slot: str, filename: str,
                             data_url: str) -> dict:
        """Заменить глобальный ассет приложения по drag&drop (п.5)."""
        if slot not in self._GLOBAL_SLOTS:
            return {"ok": False, "error": "Неизвестный слот"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        glob_dir = os.path.join(DATA, "global_assets")
        os.makedirs(glob_dir, exist_ok=True)
        rel = f"global_assets/{slot}.png"
        with open(os.path.join(DATA, rel), "wb") as f:
            f.write(raw)
        assets.clear_cache()

        # обновляем app_settings.json
        sp = os.path.join(DATA, "app_settings.json")
        with open(sp, encoding="utf-8") as f:
            cfg = json.load(f)
        section, ckey = self._GLOBAL_SLOTS[slot]
        if ckey is None:                       # preview_background
            cfg.setdefault(section, {})["file"] = rel
        else:
            cfg.setdefault(section, {}).setdefault(ckey, {})["file"] = rel
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        # обновляем in-memory globals_
        setattr(self.app.globals_, slot, rel)
        return {"ok": True, "thumb": self._thumb(self.app.global_root, rel)}

    def replace_safe_zone(self, format_key: str, filename: str,
                          data_url: str) -> dict:
        """Заменить PNG safe-zone формата; frame_rect_pct пересчитается (п.5)."""
        if format_key not in self.app.formats:
            return {"ok": False, "error": "Неизвестный формат"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        sz_dir = os.path.join(DATA, "safe_zones")
        os.makedirs(sz_dir, exist_ok=True)
        rel = f"safe_zones/{format_key}.png"
        path = os.path.join(DATA, rel)
        with open(path, "wb") as f:
            f.write(raw)
        assets.clear_cache()
        rect = assets.compute_safe_zone_rect(path)

        # обновляем formats_template.json
        tp = os.path.join(DATA, "formats_template.json")
        with open(tp, encoding="utf-8") as f:
            tpl = json.load(f)
        tpl[format_key]["safe_zone"]["source_file"] = rel
        tpl[format_key]["safe_zone"]["frame_rect_pct"] = [round(v, 4) for v in rect]
        with open(tp, "w", encoding="utf-8") as f:
            json.dump(tpl, f, ensure_ascii=False, indent=2)

        # обновляем in-memory
        self.app.formats[format_key].safe_zone.frame_rect_pct = tuple(round(v, 4)
                                                                      for v in rect)
        return {"ok": True, "thumb": self._thumb(self.app.data_root, rel),
                "frame_rect_pct": [round(v, 4) for v in rect]}

    def set_platform_legal(self, format_key: str, filename: str,
                           data_url: str) -> dict:
        """Загрузить юр.информацию площадки для формата и включить её (п.2)."""
        if not self.project:
            return {"ok": False, "error": "Нет открытого проекта"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        os.makedirs(os.path.join(self.project.workdir, "assets"), exist_ok=True)
        rel = f"assets/legal_platform_{format_key}.png"
        with open(os.path.join(self.project.workdir, rel), "wb") as f:
            f.write(raw)
        assets.clear_cache()
        node = self.project.manifest["formats"][format_key]["legal"]
        node["platform_legal_file"] = rel
        node["show_platform_legal"] = True
        return {"ok": True, "platform_legal_file": rel}

    # --- состояние для UI -------------------------------------------------

    def get_state(self) -> dict:
        """Полный снимок состояния для отрисовки интерфейса."""
        if not self.project:
            return {"loaded": False}
        formats = []
        for key in self.project.format_keys:
            fmt = self.app.formats[key]
            pf = self.project.project_format(key)
            formats.append({
                "key": key,
                "family": fmt.family,
                "family_title": FAMILY_TITLES.get(fmt.family, fmt.family),
                "orientation": fmt.orientation,
                "size_px": list(fmt.size_px),
                "visible": pf.visible,
                "supports_regions": fmt.supports_regions,
                "aspect": fmt.width / fmt.height,
                "width": fmt.width,
                "height": fmt.height,
            })
        regions = [r for r, vis in
                   self.project.manifest.get("region_visibility", {}).items() if vis]
        return {
            "loaded": True,
            "movie_title": self.project.movie_title,
            "current_format": self.current_format,
            "current_region": self.current_region,
            "regions": regions,
            "formats": formats,
            "settings": self._format_settings(self.current_format),
        }

    def _format_settings(self, key: str) -> dict:
        pf = self.project.project_format(key)        # type: ignore[union-attr]
        fmt = self.app.formats[key]
        s = pf.settings
        bounds = self.scale_bounds(key)
        eff_rating = s.rating_scale_for(self.current_region, fmt.height)
        date_variant = self._active_date_variant()
        is_now = date_variant == "now"
        title_scale = float(getattr(s, "now_title_scale_pct", None) if is_now and getattr(s, "now_title_scale_pct", None) is not None else s.title_scale_pct)
        date_scale = float(getattr(s, "now_scale_pct", None) if is_now and getattr(s, "now_scale_pct", None) is not None else s.date_scale_pct)
        rating_scale = float(getattr(s, "now_rating_scale_pct", None) if is_now and getattr(s, "now_rating_scale_pct", None) is not None else eff_rating)
        date_gap = float(getattr(s, "now_gap_title_date_pct", None) if is_now and getattr(s, "now_gap_title_date_pct", None) is not None else s.gap_title_date_pct)
        offsets = dict(getattr(s, "element_offsets", {}) or {})
        legacy = {"x_pct": float(getattr(s, "offset_x_pct", 0.0) or 0.0),
                  "y_pct": float(getattr(s, "offset_y_pct", 0.0) or 0.0)}
        for _k in ("title", "date", "date_now", "rating"):
            offsets.setdefault(_k, legacy.copy())
        bounds["offset_x"] = {"min": -25.0, "max": 25.0, "default": 0.0}
        bounds["offset_y"] = {"min": -25.0, "max": 25.0, "default": 0.0}
        return {
            "key": key,
            "orientation": fmt.orientation,
            "legal_is_vertical": fmt.legal_is_vertical,
            "canvas_w": fmt.width, "canvas_h": fmt.height,
            "title_scale_pct": title_scale,
            "date_scale_pct": date_scale,
            "rating_scale_pct": rating_scale,
            "gap_title_date_pct": date_gap,
            "offset_x_pct": offsets.get("date_now" if is_now else "date", {}).get("x_pct", 0.0),
            "offset_y_pct": offsets.get("date_now" if is_now else "date", {}).get("y_pct", 0.0),
            "element_offsets": offsets,
            "active_position_element": "date_now" if is_now else "title",
            "bounds": bounds,
            "legal": asdict(pf.legal),
            "zoom": self._zoom.get(key, 100),
            "px_ctx": self._px_ctx(key),
            # п.4: расширенные настройки плашки + наличие региональных элементов
            "display": {
                "show_title": getattr(s, "show_title", True),
                "show_date": getattr(s, "show_date", True),
                "show_rating": getattr(s, "show_rating", True),
                "swap_title_rating": getattr(s, "swap_title_rating", False),
                "shadow_enabled": getattr(s, "shadow_enabled", False),
                "shadow_blur_pct": getattr(s, "shadow_blur_pct", 0.45),
                "shadow_distance_pct": getattr(s, "shadow_distance_pct", 0.55),
                "shadow_opacity_pct": getattr(s, "shadow_opacity_pct", 55.0),
            },
            "available": {
                "date": self.project.region_date(self.current_region) is not None,
                "rating": self.project.region_rating(self.current_region) is not None,
                "date_now": self.has_date_now(self.current_region),
                "date_active": date_variant,
                "export_now_enabled": bool(getattr(s, "export_now_enabled", True)),
            },
        }

    def _px_ctx(self, key: str) -> dict:
        """Контент-размеры (по content_bbox) и оси для пересчёта occupancy% → px."""
        fmt = self.app.formats[key]
        region = self.current_region
        out = {"W": fmt.width, "H": fmt.height}

        def el_ctx(elem):
            if elem is None:
                return None
            path = os.path.join(self.project.workdir, elem.file)  # type: ignore[union-attr]
            x0, y0, x1, y1 = assets.content_bbox_pct(path)
            iw, ih = assets.load_rgba(path).size
            return {"cw": (x1 - x0) * iw, "ch": (y1 - y0) * ih,
                    "axis": elem.anchor_axis}

        out["title"] = el_ctx(self.project.title_element)
        out["date"] = el_ctx(self.project.region_date(region))
        out["rating"] = el_ctx(self.project.region_rating(region))
        return out

    # --- превью -----------------------------------------------------------

    def render_preview(self, format_key: str, with_bg: bool = False,
                       with_safe: bool = False, with_aim: bool = False,
                       fast: bool = False) -> str:
        assert self.project
        region = self.current_region
        # подменяем регион формата на активный (для соцсетей)
        self._apply_region(format_key, region)
        img = self.project.render(self.app, format_key,
                                  with_background=with_bg, with_safe_zone=with_safe,
                                  with_aim=with_aim)
        return _png_b64(img, LIVE_MAX if fast else PREVIEW_MAX, fast=fast)

    def _apply_region(self, format_key: str, region: str) -> None:
        fmt = self.app.formats[format_key]
        if fmt.supports_regions:
            self.project.manifest["formats"][format_key]["region"] = region  # type: ignore[union-attr]

    # --- редактирование настроек (real-time) ------------------------------

    def set_setting(self, format_key: str, field: str, value: float,
                    with_bg: bool = False, with_safe: bool = False, with_aim: bool = False,
                    record_history: bool = True, history_old=None) -> str:
        """Изменить scale/gap/position и сразу вернуть live-превью."""
        assert self.project
        node = self.project.manifest["formats"][format_key]["settings"]
        value = float(value)

        def push(entry: dict) -> None:
            if not record_history:
                return
            if history_old is not None:
                entry["old"] = copy.deepcopy(history_old)
            if entry.get("old") == entry.get("new"):
                return
            self._push_undo(entry)

        if field in {"offset_x_pct", "offset_y_pct"}:
            # старое имя поля оставлено для UI как быстрый путь: меняем активный
            # элемент, а не весь творческий блок.
            target = self._active_position_element(field)
            axis = "x_pct" if field == "offset_x_pct" else "y_pct"
            offsets = node.setdefault("element_offsets", {})
            cur = dict(offsets.get(target, {}))
            old = copy.deepcopy(offsets.get(target))
            cur[axis] = value
            offsets[target] = cur
            push({"format": format_key, "field": "element_offsets",
                  "element": target, "old": old,
                  "new": copy.deepcopy(offsets.get(target))})
        elif field.startswith("offset_") and field.endswith(("_x_pct", "_y_pct")):
            # Новая явная форма: offset_title_x_pct / offset_date_now_y_pct …
            rest = field[len("offset_"):-len("_pct")]
            if rest.endswith("_x"):
                target, axis = rest[:-2], "x_pct"
            elif rest.endswith("_y"):
                target, axis = rest[:-2], "y_pct"
            else:
                target, axis = "title", "x_pct"
            offsets = node.setdefault("element_offsets", {})
            cur = dict(offsets.get(target, {}))
            old = copy.deepcopy(offsets.get(target))
            cur[axis] = value
            offsets[target] = cur
            push({"format": format_key, "field": "element_offsets",
                  "element": target, "old": old,
                  "new": copy.deepcopy(offsets.get(target))})
        elif field == "title_scale_pct" and self._active_date_variant() == "now":
            old = copy.deepcopy(node.get("now_title_scale_pct"))
            node["now_title_scale_pct"] = value
            push({"format": format_key, "field": "now_title_scale_pct",
                  "region": None, "old": old, "new": value})
        elif field == "date_scale_pct" and self._active_date_variant() == "now":
            old = copy.deepcopy(node.get("now_scale_pct"))
            node["now_scale_pct"] = value
            push({"format": format_key, "field": "now_scale_pct",
                  "region": None, "old": old, "new": value})
        elif field == "rating_scale_pct" and self._active_date_variant() == "now":
            old = copy.deepcopy(node.get("now_rating_scale_pct"))
            node["now_rating_scale_pct"] = value
            push({"format": format_key, "field": "now_rating_scale_pct",
                  "region": None, "old": old, "new": value})
        elif field == "gap_title_date_pct" and self._active_date_variant() == "now":
            old = copy.deepcopy(node.get("now_gap_title_date_pct"))
            node["now_gap_title_date_pct"] = value
            push({"format": format_key, "field": "now_gap_title_date_pct",
                  "region": None, "old": old, "new": value})
        elif field == "rating_scale_pct" and self.current_region != "RU":
            by = node.setdefault("rating_scale_by_region", {})
            old = copy.deepcopy(by.get(self.current_region))
            by[self.current_region] = value
            push({"format": format_key, "field": field,
                  "region": self.current_region,
                  "old": old, "new": value})
        else:
            old = copy.deepcopy(node.get(field))
            node[field] = value
            push({"format": format_key, "field": field,
                  "region": None, "old": old, "new": value})
        return self.render_preview(format_key, with_bg, with_safe, with_aim, fast=True)

    def _active_position_element(self, field: str = "") -> str:
        """Какой элемент сейчас двигают старые offset_x/y-поля."""
        # Значение выбирает фронтенд через set_position_target; фолбэк: активная
        # дата двигает date/date_now, иначе title.
        target = getattr(self, "_position_target", None)
        if target in {"title", "date", "date_now", "rating"}:
            return target
        return "date_now" if self._active_date_variant() == "now" else "title"

    def set_position_target(self, target: str) -> dict:
        if target not in {"title", "date", "date_now", "rating"}:
            target = "title"
        self._position_target = target
        return self._format_settings(self.current_format)


    def _content_dims(self, elem) -> Optional[tuple[float, float]]:
        if elem is None or not self.project:
            return None
        path = os.path.join(self.project.workdir, elem.file)
        x0, y0, x1, y1 = assets.content_bbox_pct(path)
        iw, ih = assets.load_rgba(path).size
        return ((x1 - x0) * iw, (y1 - y0) * ih)

    def _render_dims(self, elem, scale_pct: float, W: int, H: int) -> Optional[tuple[float, float]]:
        dims = self._content_dims(elem)
        if not dims:
            return None
        cw, ch = dims
        f = geo.occupancy_scale_factor(cw, ch, float(scale_pct), elem.anchor_axis, W, H)
        return (cw * f, ch * f)

    def _scale_for_target_width(self, elem, target_w: float, W: int, H: int) -> Optional[float]:
        dims = self._content_dims(elem)
        if not dims or target_w <= 0:
            return None
        cw, ch = dims
        if cw <= 0 or ch <= 0:
            return None
        if elem.anchor_axis == "height":
            target_h = target_w * (ch / cw)
            return target_h / H * 100.0
        return target_w / W * 100.0

    def _scale_for_target_height(self, elem, target_h: float, W: int, H: int) -> Optional[float]:
        dims = self._content_dims(elem)
        if not dims or target_h <= 0:
            return None
        cw, ch = dims
        if cw <= 0 or ch <= 0:
            return None
        if elem.anchor_axis == "height":
            return target_h / H * 100.0
        target_w = target_h * (cw / ch)
        return target_w / W * 100.0

    def _creative_quarters(self, format_key: str) -> tuple:
        """Вернуть quarter для title и rating с учётом mirror/swap."""
        fmt = self.app.formats[format_key]
        pf = self.project.project_format(format_key)
        frame = geo.frame_rect_px(fmt.safe_zone.frame_rect_pct, fmt.width, fmt.height)
        q = geo.split_quarters(frame)
        effective_mirror = fmt.title_mirrored ^ bool(getattr(pf.settings, "swap_title_rating", False))
        return (q.top_right, q.top_left) if effective_mirror else (q.top_left, q.top_right)


    # --- автопропагация: первое касание настроек за сессию (п. v0.10) --------
    _session_calibrated = False

    def sync_from(self, source_key: str, variant: str = "") -> dict:
        """Ручная синхронизация композиции с текущего формата на остальные.

        Синхронизируем не «голые проценты», а видимые пропорции:
        дата сохраняет размер относительно названия, отступ сохраняет долю от
        высоты названия, ВО сохраняет высоту относительно названия. Так соцсети
        и BYYD больше не «гуляют» из-за разных размеров холста и safe-zone.
        """
        if not self.project:
            return {"ok": False, "count": 0}
        if source_key != "16x9" or self.app.formats.get(source_key).family != "social":
            return {"ok": False, "count": 0,
                    "error": "Синхронизация доступна только на формате соцсетей 16×9"}
        src_node = self.project.manifest["formats"].get(source_key)
        if not src_node:
            return {"ok": False, "count": 0}
        src = src_node["settings"]
        src_region = self.current_region
        variant = "now" if variant == "now" else "date"
        is_now = variant == "now"
        sync_rating = True
        src_fmt = self.app.formats[source_key]
        src_pf = self.project.project_format(source_key)
        SW, SH = src_fmt.width, src_fmt.height

        def title_rating_quarters(fmt, pf):
            W, H = fmt.width, fmt.height
            frame = geo.frame_rect_px(fmt.safe_zone.frame_rect_pct, W, H)
            qs = geo.split_quarters(frame)
            mirror = fmt.title_mirrored ^ bool(getattr(pf.settings, "swap_title_rating", False))
            return (qs.top_right, qs.top_left) if mirror else (qs.top_left, qs.top_right)

        src_title_q, src_rating_q = title_rating_quarters(src_fmt, src_pf)

        title = self.project.title_element

        def date_element_for(region: str, wanted: str):
            from .engine.models import Element
            node = self.project.manifest["elements"]["by_region"].get(region)
            item = ((((node or {}).get("date_variants") or {}).get("items") or {}).get(wanted))
            if item is None and wanted == "now" and region != "RU":
                ru = self.project.manifest["elements"]["by_region"].get("RU")
                item = ((((ru or {}).get("date_variants") or {}).get("items") or {}).get("now"))
            return Element.from_dict(item) if item else None

        date = date_element_for(src_region, variant)
        if is_now and date is None:
            return {"ok": False, "count": 0, "error": "Версия «Уже в кино» не загружена"}
        rating = self.project.region_rating(src_region)
        src_title_scale = float((src.get("now_title_scale_pct") if is_now else src.get("title_scale_pct"))
                                or src.get("title_scale_pct", src_pf.settings.title_scale_pct))
        src_date_scale = float((src.get("now_scale_pct") if is_now else src.get("date_scale_pct"))
                               or src.get("date_scale_pct", src_pf.settings.date_scale_pct))
        src_gap = float((src.get("now_gap_title_date_pct") if is_now else src.get("gap_title_date_pct"))
                        or src.get("gap_title_date_pct", src_pf.settings.gap_title_date_pct))
        src_rating_base = (src.get("rating_scale_by_region", {}) or {}).get(
            src_region, src_pf.settings.rating_scale_for(src_region, SH))
        src_rating_scale = float((src.get("now_rating_scale_pct") if is_now else src_rating_base)
                                 or src_rating_base)

        src_title_dims = self._render_dims(title, src_title_scale, SW, SH)
        src_date_dims = self._render_dims(date, src_date_scale, SW, SH)
        src_rating_dims = self._render_dims(rating, src_rating_scale, SW, SH)
        date_to_title_w = (src_date_dims[0] / src_title_dims[0]
                           if src_title_dims and src_date_dims and src_title_dims[0] else None)
        gap_to_title_h = ((src_gap / 100.0 * SH) / src_title_dims[1]
                          if src_title_dims and src_title_dims[1] else None)
        rating_to_title_h = (src_rating_dims[1] / src_title_dims[1]
                             if src_title_dims and src_rating_dims and src_title_dims[1] else None)

        creative_shrink = 0.88  # v19: после синхронизации title/date/now чуть компактнее, ВО не трогаем
        count = 0
        for key in self.project.format_keys:
            if key == source_key:
                continue
            tgt_node = self.project.manifest["formats"][key]
            fmt = self.app.formats[key]
            if fmt.supports_regions:
                tgt_node["region"] = src_region
            elif src_region != "RU":
                continue
            tgt = tgt_node.setdefault("settings", {})
            bounds = self.scale_bounds_for(key, src_region, variant)
            W, H = fmt.width, fmt.height

            # 1) Название — базовая опора композиции. Переносим не голое число
            # из 16×9, а коэффициент относительно штатного масштаба исходного
            # формата. Иначе вертикальные/KZ-форматы получают слишком маленький
            # title_scale и всё остальное уменьшается вслед за ним.
            src_title_default = (_SCALE_DEFAULTS.get(source_key) or (src_title_scale,))[0] or src_title_scale
            tgt_title_default = (_SCALE_DEFAULTS.get(key) or (src_title_scale,))[0]
            title_scale = float(tgt_title_default) * (src_title_scale / float(src_title_default or 1.0)) * creative_shrink
            tgt_title_q, tgt_rating_q = title_rating_quarters(fmt, self.project.project_format(key))
            if bounds.get("title"):
                title_scale = self._clamp_safe_max(title_scale, bounds["title"])
                if is_now:
                    tgt["now_title_scale_pct"] = title_scale
                else:
                    tgt["title_scale_pct"] = title_scale
            tgt_title_dims = self._render_dims(title, title_scale, W, H)

            tgt_date = date_element_for(src_region if is_now else src_region, variant)
            # 2) Дата/«Уже в кино» — тот же видимый размер относительно title.
            # Если выбранный на исходнике title слишком крупный для сохранения
            # пары title+date в safe-zone целевого формата, уменьшаем title, а
            # не ломаем отношение date/title. Так синхрон с 9×16 не заставляет
            # дату «гулять» относительно названия.
            date_val = None
            if date_to_title_w and tgt_title_dims and tgt_date and bounds.get("date"):
                raw = self._scale_for_target_width(tgt_date, tgt_title_dims[0] * date_to_title_w, W, H)
                if raw is not None:
                    dmax = float(bounds["date"].get("max", raw))
                    if raw > dmax:
                        max_date_dims = self._render_dims(tgt_date, dmax, W, H)
                        if max_date_dims and date_to_title_w:
                            target_title_w = max_date_dims[0] / date_to_title_w
                            adj = self._scale_for_target_width(title, target_title_w, W, H)
                            if adj is not None:
                                title_scale = self._clamp(adj, bounds.get("title", {"min": adj, "max": adj}))
                                if is_now:
                                    tgt["now_title_scale_pct"] = title_scale
                                else:
                                    tgt["title_scale_pct"] = title_scale
                                tgt_title_dims = self._render_dims(title, title_scale, W, H)
                                raw = self._scale_for_target_width(tgt_date, tgt_title_dims[0] * date_to_title_w, W, H)
                    date_val = self._clamp_safe_max(raw, bounds["date"])
            else:
                raw_scale = src.get("now_scale_pct", src.get("date_scale_pct")) if is_now else src.get("date_scale_pct")
                if raw_scale is not None and bounds.get("date"):
                    date_val = self._clamp_safe_max(float(raw_scale), bounds["date"])
            if date_val is not None:
                if is_now:
                    tgt["now_scale_pct"] = date_val
                else:
                    tgt["date_scale_pct"] = date_val

            # 3) Отступ — доля от высоты title, а не процент от разного холста.
            if gap_to_title_h is not None and tgt_title_dims:
                gap_pct = (gap_to_title_h * tgt_title_dims[1]) / H * 100.0
            else:
                gap_pct = src_gap
            if is_now:
                tgt["now_gap_title_date_pct"] = round(float(gap_pct), 3)
            else:
                tgt["gap_title_date_pct"] = round(float(gap_pct), 3)

            # 4) ВО — масштаб переносим как коэффициент относительно
            # штатного масштаба региона/формата. Для KZ это особенно важно:
            # базовый ВО задан как 135 px на каждом холсте, а перенос через
            # title-height делал вертикальные форматы слишком мелкими.
            if sync_rating and bounds.get("rating"):
                if src_region == "KZ":
                    src_rating_default = KZ_DEFAULT_RATING_PX / SH * 100.0
                    tgt_rating_default = KZ_DEFAULT_RATING_PX / H * 100.0
                else:
                    src_rating_default = (_SCALE_DEFAULTS.get(source_key) or (0, 0, src_rating_scale))[2] or src_rating_scale
                    tgt_rating_default = (_SCALE_DEFAULTS.get(key) or (0, 0, src_rating_scale))[2]
                rating_val = float(tgt_rating_default) * (src_rating_scale / float(src_rating_default or 1.0))
                rating_val = self._clamp_safe_max(rating_val, bounds["rating"])
                if is_now:
                    tgt["now_rating_scale_pct"] = rating_val
                elif src_region == "RU":
                    tgt["rating_scale_pct"] = rating_val
                else:
                    tgt.setdefault("rating_scale_by_region", {})[src_region] = rating_val

            # 5) X/Y не синхронизируем. Это намеренно: offsets живут в
            # процентах холста, а UI показывает их ещё и в пикселях. Копирование
            # процентов между разными размерами холста визуально меняло
            # пиксельные значения после нажатия «Синхронизировать».
            count += 1
        # Синхронизация — осознанное действие пользователя: сохраняем проект сразу,
        # чтобы экспорт и повторное открытие видели уже пересчитанные форматы.
        try:
            if self.project.path:
                self.project.save(self.project.path)
        except Exception:
            pass
        return {"ok": True, "propagated": True, "count": count}

    def propagate_composition(self, source_key: str) -> dict:
        # Старый API больше не вызывается автоматически, но оставлен как alias.
        return self.sync_from(source_key)

    @staticmethod
    def _clamp(v: float, b: dict) -> float:
        return round(max(float(b.get("min", v)), min(float(b.get("max", v)), v)), 2)

    @staticmethod
    def _clamp_safe_max(v: float, b: dict) -> float:
        """Для синхронизации сохраняем пропорции и ограничиваем только верхом safe-zone."""
        return round(max(0.0, min(float(b.get("max", v)), float(v))), 3)

    def scale_bounds_for(self, format_key: str, region: str, date_variant: str = "date") -> dict:
        old_region = self.current_region
        try:
            self.current_region = region
            return self.scale_bounds(format_key, date_variant=date_variant)
        finally:
            self.current_region = old_region

    def reset_session_calibration(self) -> None:
        """Сбросить флаг для новой сессии."""
        self._session_calibrated = False

    def set_legal(self, format_key: str, field: str, value) -> dict:
        assert self.project
        self.project.manifest["formats"][format_key]["legal"][field] = value
        return self._format_settings(format_key)

    def set_display(self, format_key: str, field: str, value) -> dict:
        """Расширенные настройки плашки (п.4): show_title/show_date/show_rating
        и swap_title_rating. Пишутся в settings формата."""
        assert self.project
        allowed = {"show_title", "show_date", "show_rating", "swap_title_rating", "shadow_enabled"}
        if field not in allowed:
            return {"ok": False, "error": "Неизвестное поле"}
        node = self.project.manifest["formats"][format_key]["settings"]
        old = copy.deepcopy(node.get(field))
        node[field] = bool(value)
        # Расширенные настройки тоже должны попадать в undo/redo (иначе на них
        # «отменять нечего»). Формат entry совместим с _apply_history_value.
        if node[field] != old:
            self._push_undo({"format": format_key, "field": field,
                             "old": old, "new": node[field]})
        return self._format_settings(format_key)

    def switch_region(self, region: str) -> dict:
        self.current_region = region
        if self.project:
            self.project.manifest["active_region"] = region
        return self.get_state()

    def switch_format(self, format_key: str) -> dict:
        self.current_format = format_key
        return {"settings": self._format_settings(format_key)}

    def toggle_visible(self, format_key: str) -> dict:
        node = self.project.manifest["formats"][format_key]  # type: ignore[union-attr]
        node["visible"] = not node.get("visible", True)
        return {"visible": node["visible"]}

    def set_visible(self, format_key: str, value: bool) -> dict:
        """Явно задать видимость формата (нужно для соло-режима, п.4)."""
        node = self.project.manifest["formats"][format_key]  # type: ignore[union-attr]
        node["visible"] = bool(value)
        return {"visible": node["visible"]}

    def set_zoom(self, format_key: str, zoom_pct: int) -> None:
        self._zoom[format_key] = int(zoom_pct)

    def has_region_date(self, region: str) -> bool:
        """Есть ли загруженная дата для региона (п.4 v0.5.2)."""
        if not self.project:
            return False
        return self.project.region_date(region) is not None

    def import_date_now(self, region: str, filename: str, data_url: str) -> dict:
        """Импорт/замена варианта «Уже в кино» для региона."""
        if not self.project:
            return {"ok": False, "error": "Нет открытого проекта"}
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Не удалось прочитать файл: {e}"}
        # сохраняем во временный date-слот через общую валидацию, затем переносим
        old_node = self.project.manifest["elements"]["by_region"].get(region, {}).get("date_variants")
        old_active = (old_node or {}).get("active", "date")
        old_date = ((old_node or {}).get("items", {}) or {}).get("date")
        res = self.project.import_asset("date", region, filename, raw)
        if not res.get("ok"):
            return res
        node = self.project.manifest["elements"]["by_region"].setdefault(region, {})
        dv = node.setdefault("date_variants", {"active": "date", "items": {}})
        imported_date = dv.get("items", {}).get("date")
        if imported_date:
            dv.setdefault("items", {})["now"] = imported_date.copy()
        if old_date:
            dv["items"]["date"] = old_date
        dv["active"] = old_active if old_active in dv.get("items", {}) else "date"
        return {"ok": True, **res}

    def switch_date_variant(self, variant: str) -> dict:
        """Переключить active-вариант даты (date / now). RU-now доступен и для KZ."""
        if not self.project:
            return {"ok": False}
        variant = "now" if variant == "now" else "date"
        node = self.project.manifest["elements"]["by_region"].setdefault(self.current_region, {})
        dv = node.setdefault("date_variants", {"active": "date", "items": {}})
        items = dv.setdefault("items", {})
        if variant == "now" and "now" not in items:
            ru = self.project.manifest["elements"]["by_region"].get("RU")
            ru_items = (((ru or {}).get("date_variants") or {}).get("items") or {})
            if "now" not in ru_items:
                return {"ok": False}
        elif variant == "date" and "date" not in items:
            return {"ok": False}
        # Первое переключение получает геометрию текущего режима как стартовую,
        # дальше режимы редактируются и синхронизируются независимо.
        for fmt_node in self.project.manifest.get("formats", {}).values():
            st = fmt_node.setdefault("settings", {})
            if variant == "now":
                st.setdefault("now_title_scale_pct", st.get("title_scale_pct"))
                st.setdefault("now_scale_pct", st.get("date_scale_pct"))
                st.setdefault("now_rating_scale_pct", st.get("rating_scale_pct"))
                st.setdefault("now_gap_title_date_pct", st.get("gap_title_date_pct"))
            else:
                st.setdefault("title_scale_pct", st.get("now_title_scale_pct"))
                st.setdefault("date_scale_pct", st.get("now_scale_pct"))
                st.setdefault("rating_scale_pct", st.get("now_rating_scale_pct"))
                st.setdefault("gap_title_date_pct", st.get("now_gap_title_date_pct"))
        dv["active"] = variant
        assets.clear_cache()
        return {"ok": True, "active": variant}

    def has_date_now(self, region: str) -> bool:
        """Есть ли вариант «Уже в кино» для региона. Для KZ используем RU-слот."""
        if not self.project:
            return False
        node = self.project.manifest["elements"]["by_region"].get(region)
        if node and node.get("date_variants") and "now" in node["date_variants"].get("items", {}):
            return True
        if region != "RU":
            ru = self.project.manifest["elements"]["by_region"].get("RU")
            return bool(ru and ru.get("date_variants") and "now" in ru["date_variants"].get("items", {}))
        return False


    def has_export_now(self) -> bool:
        """Есть ли пользовательская картинка «Уже в кино» для экспорта."""
        if not self.project:
            return False
        return self.has_date_now("RU")

    def has_platform_legal(self) -> bool:
        """Есть ли хотя бы один загруженный файл юр.информации площадки."""
        if not self.project:
            return False
        for node in self.project.manifest.get("formats", {}).values():
            legal = node.get("legal", {})
            if legal.get("platform_legal_file"):
                return True
        return False

    def _active_date_variant(self) -> str:
        if not self.project:
            return "date"
        node = self.project.manifest["elements"]["by_region"].get(self.current_region)
        if node and node.get("date_variants"):
            active = node["date_variants"].get("active", "date")
            if active == "now" and self.has_date_now(self.current_region):
                return "now"
            if active in node["date_variants"].get("items", {}):
                return active
        return "date"

    def get_element_thumb(self, kind: str, region: str, variant: str = "") -> Optional[str]:
        """Превью элемента (для модалки замены). variant='now' показывает «Уже в кино»."""
        if not self.project:
            return None
        elem = None
        if kind == "title":
            elem = self.project.title_element
        elif kind == "date":
            # В модалке замены превью должно показывать конкретный слот, а не
            # активный вариант. Иначе при включённом «Уже в кино» строка «Дата»
            # тоже показывала картинку «Уже в кино».
            node = self.project.manifest["elements"]["by_region"].get(region)
            items = (((node or {}).get("date_variants") or {}).get("items") or {})
            item = items.get("now" if variant == "now" else "date")
            if item is None and variant == "now" and region != "RU":
                ru = self.project.manifest["elements"]["by_region"].get("RU")
                item = ((((ru or {}).get("date_variants") or {}).get("items") or {}).get("now"))
            from .engine.models import Element
            elem = Element.from_dict(item) if item else None
        elif kind == "rating":
            elem = self.project.region_rating(region)
        if elem is None:
            return None
        return self._thumb(self.project.workdir, elem.file, max_side=300)

    def remove_element(self, kind: str, region: str = "RU", variant: str = "") -> dict:
        """Удалить импортированный проектный материал. Глобальные ассеты не трогаем."""
        if not self.project:
            return {"ok": False, "error": "Нет открытого проекта"}
        els = self.project.manifest["elements"]
        if kind == "title":
            els["title"] = None
        elif kind == "date":
            reg = els["by_region"].setdefault(region, {})
            dv = reg.get("date_variants")
            if dv and variant == "now":
                dv.get("items", {}).pop("now", None)
                if dv.get("active") == "now":
                    dv["active"] = "date"
            elif dv:
                dv.get("items", {}).pop("date", None)
                if not dv.get("items"):
                    reg["date_variants"] = None
                elif dv.get("active") == "date":
                    dv["active"] = next(iter(dv["items"]))
        elif kind == "rating":
            els["by_region"].setdefault(region, {})["rating"] = None
        elif kind == "background":
            self.project.manifest.setdefault("project_meta", {}).pop("preview_background", None)
        assets.clear_cache()
        return {"ok": True}

    def get_project_background_thumb(self) -> Optional[str]:
        """Превью текущего фона проекта для модалки замены (п.6)."""
        if not self.project:
            return None
        rel = self.project.manifest.get("project_meta", {}).get("preview_background")
        if not rel:
            return None
        return self._thumb(self.project.workdir, rel, max_side=300)

    # --- границы ползунков (п.31 + п.7) -----------------------------------

    def scale_bounds(self, format_key: str, date_variant: str = "") -> dict:
        """min/max/default occupancy % для title/date/rating с учётом четвертей."""
        assert self.project
        fmt = self.app.formats[format_key]
        pf = self.project.project_format(format_key)
        W, H = fmt.width, fmt.height
        frame = geo.frame_rect_px(fmt.safe_zone.frame_rect_pct, W, H)
        q = geo.split_quarters(frame)
        region = self.current_region

        out: dict[str, dict] = {}

        # учитываем зеркальную раскладку (п.8)
        effective_mirror = fmt.title_mirrored ^ bool(getattr(pf.settings, "swap_title_rating", False))
        if effective_mirror:
            title_q, rating_q = q.top_right, q.top_left
        else:
            title_q, rating_q = q.top_left, q.top_right

        def bounds_for(elem, default_pct, quarter, low_mult=0.80, floor_pct=0.0, high_mult=1.40):
            if elem is None:
                return None
            path = os.path.join(self.project.workdir, elem.file)  # type: ignore[union-attr]
            x0, y0, x1, y1 = assets.content_bbox_pct(path)
            iw, ih = assets.load_rgba(path).size
            cw, ch = (x1 - x0) * iw, (y1 - y0) * ih
            low, high = geo.scale_bounds_pct(default_pct, cw, ch, quarter,
                                             elem.anchor_axis, W, H,
                                             low_mult=low_mult, floor_pct=floor_pct,
                                             high_mult=high_mult)
            return {"min": low, "max": high, "default": default_pct}

        title_default = (_SCALE_DEFAULTS.get(format_key) or (pf.settings.title_scale_pct,))[0]
        out["title"] = bounds_for(self.project.title_element,
                                  title_default, title_q,
                                  low_mult=0.2, floor_pct=1.0, high_mult=10.0)
        def _date_for_variant(region_name: str, wanted: str):
            node = self.project.manifest["elements"]["by_region"].get(region_name)
            item = ((((node or {}).get("date_variants") or {}).get("items") or {}).get(wanted))
            if item is None and wanted == "now" and region_name != "RU":
                ru = self.project.manifest["elements"]["by_region"].get("RU")
                item = ((((ru or {}).get("date_variants") or {}).get("items") or {}).get("now"))
            return Element.from_dict(item) if item else None

        active_variant = date_variant or self._active_date_variant()
        date = _date_for_variant(region, active_variant)
        # Дату/«Уже в кино» ограничиваем по ширине четверти названия, потому что
        # элемент центрируется под title и иначе при синхронизации может вылететь.
        if date is not None:
            d = float(getattr(pf.settings, "now_scale_pct", None) if active_variant == "now" and getattr(pf.settings, "now_scale_pct", None) is not None else pf.settings.date_scale_pct)
            date_default = (_SCALE_DEFAULTS.get(format_key) or (0, d))[1]
            b = bounds_for(date, date_default, title_q, low_mult=0.35, floor_pct=0.0, high_mult=10.0)
            if b is None:
                b = {"min": round(d * 0.8, 2), "max": round(d * 2.5, 2), "default": d}
            # Дополнительный кэп: title+date считаются как единый блок.
            # Максимум даты теперь может быть больше названия, но общий блок
            # обязан оставаться внутри title-четверти safe-zone по ширине и
            # высоте. Это даёт большой запас scale без вылета за рамки.
            try:
                title = self.project.title_element
                if title is not None:
                    t_dims = self._render_dims(title, pf.settings.title_scale_pct, W, H)
                    d_path = os.path.join(self.project.workdir, date.file)
                    dx0, dy0, dx1, dy1 = assets.content_bbox_pct(d_path)
                    diw, dih = assets.load_rgba(d_path).size
                    dcw, dch = (dx1 - dx0) * diw, (dy1 - dy0) * dih
                    # Ширина: дата может быть шире title, но не шире четверти.
                    max_w = max(1.0, title_q.w * 0.98)
                    if date.anchor_axis == "height":
                        aspect = dcw / dch if dch else 1.0
                        max_pct_w = (max_w / aspect) / H * 100.0 if aspect else b["max"]
                    else:
                        max_pct_w = max_w / W * 100.0
                    # Высота: title + gap + date должны помещаться по вертикали.
                    gap = float(getattr(pf.settings, "now_gap_title_date_pct", None) if active_variant == "now" and getattr(pf.settings, "now_gap_title_date_pct", None) is not None else pf.settings.gap_title_date_pct)
                    title_h = t_dims[1] if t_dims else 0.0
                    max_h = max(1.0, (title_q.h - title_h - gap / 100.0 * H) * 0.98)
                    if date.anchor_axis == "height":
                        max_pct_h = max_h / H * 100.0
                    else:
                        aspect_h = dch / dcw if dcw else 1.0
                        max_pct_h = (max_h / aspect_h) / W * 100.0 if aspect_h else b["max"]
                    b["max"] = round(max(b["min"], min(b["max"], max_pct_w, max_pct_h)), 2)
            except Exception:
                pass
            out["date"] = b
        rating = self.project.region_rating(region)
        # ВО/рейтинг: масштаб региональный (п.8). Дефолт берём эффективный для
        # активного региона: KZ по умолчанию = 135×135 px, RU = общий процент.
        rating_default = (KZ_DEFAULT_RATING_PX / H * 100.0) if region == "KZ" else ((_SCALE_DEFAULTS.get(format_key) or (0, 0, pf.settings.rating_scale_for(region, H)))[2])
        # п.2 v0.14: ВО можно увеличивать до 2,5× дефолта (с поправкой на предел
        # четверти, чтобы не было перекрытий). Прежнее половинное урезание для RU
        # снято — пользователь хотел больший запас по увеличению.
        out["rating"] = bounds_for(rating, rating_default, rating_q,
                                   low_mult=0.15, floor_pct=1.0, high_mult=10.0)
        gap_default = (_SCALE_DEFAULTS.get(format_key) or (0, 0, 0, pf.settings.gap_title_date_pct))[3]
        out["gap"] = {"min": 0.0, "max": 8.0, "default": gap_default}
        return out

    # --- undo / redo (сессионный, только редактирование) -------------------

    def _push_undo(self, entry: dict) -> None:
        self._undo.append(copy.deepcopy(entry))
        self._redo.clear()
        if len(self._undo) > 200:
            self._undo.pop(0)

    def _apply_history_value(self, entry: dict, value_key: str) -> None:
        """Применить old/new из entry к manifest без создания нового history."""
        node = self.project.manifest["formats"][entry["format"]]["settings"]
        value = copy.deepcopy(entry.get(value_key))
        if entry.get("field") == "element_offsets":
            offsets = node.setdefault("element_offsets", {})
            element = entry.get("element")
            if value is None:
                offsets.pop(element, None)
            else:
                offsets[element] = value
        elif entry.get("region"):
            by = node.setdefault("rating_scale_by_region", {})
            if value is None:
                by.pop(entry["region"], None)
            else:
                by[entry["region"]] = value
        else:
            if value is None:
                node.pop(entry["field"], None)
            else:
                node[entry["field"]] = value

    def undo(self) -> Optional[dict]:
        if not self._undo or not self.project:
            return None
        e = self._undo.pop()
        self._apply_history_value(e, "old")
        self._redo.append(copy.deepcopy(e))
        self.current_format = e.get("format", self.current_format)
        return {"settings": self._format_settings(e["format"]), "format": e["format"]}

    def redo(self) -> Optional[dict]:
        if not self._redo or not self.project:
            return None
        e = self._redo.pop()
        self._apply_history_value(e, "new")
        self._undo.append(copy.deepcopy(e))
        self.current_format = e.get("format", self.current_format)
        return {"settings": self._format_settings(e["format"]), "format": e["format"]}

    # --- экспорт ----------------------------------------------------------

    def set_export_now_enabled(self, enabled: bool) -> dict:
        """Разрешить/запретить экспорт варианта «Уже в кино» для проекта."""
        if not self.project:
            return {"ok": False}
        for node in self.project.manifest["formats"].values():
            node.setdefault("settings", {})["export_now_enabled"] = bool(enabled)
        return {"ok": True, "enabled": bool(enabled)}


    def export(self, out_dir: str, formats_include: list[str],
               export_png: bool = True, export_jpeg: bool = False,
               export_psd: bool = False, strip_legal: bool = False,
               export_now: bool = True, export_platform_legal: bool = False) -> dict:
        assert self.project
        opts = ExportOptions(
            formats_include=formats_include,
            export_png=export_png,
            export_jpeg_for_approval=export_jpeg,
            export_psd=export_psd,
            strip_legal=strip_legal,
            export_now=bool(export_now),
            export_platform_legal=bool(export_platform_legal),
            out_dir=out_dir,
        )
        written = export(self.project, self.app, opts)
        return {"count": len(written), "files": written, "out_dir": out_dir}


def _decode_data_url(data_url: str) -> bytes:
    """Из 'data:image/png;base64,XXXX' (или чистого base64) получить байты."""
    s = data_url or ""
    if "," in s and s.strip().lower().startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _load_recents() -> list[dict]:
    try:
        with open(RECENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_recents(items: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(RECENTS_FILE), exist_ok=True)
        with open(RECENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["Api"]
