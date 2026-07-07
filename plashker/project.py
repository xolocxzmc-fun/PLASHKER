"""
Plashker — слой проекта: .plshk-контейнер, конфиги, экспорт.

.plshk — zip-архив (п.8) с manifest.json и assets/ внутри. Глобальные
ассеты приложения и шаблон форматов лежат ВНЕ проекта и подтягиваются
централизованно (п.2, раздел 0).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .engine import (
    Element,
    FormatDef,
    GlobalAssets,
    ProjectFormat,
    RenderContext,
    assets,
    render_format,
)

# каталоги соцсетей при экспорте (п.32): 4 формата + общий byyd/
SOCIAL_FORMATS = ("16x9", "9x16", "4x5", "1x1")

# дефолты scale (title, date, rating, gap) из таблицы спеки — для новых проектов
_SCALE_DEFAULTS = {
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


def _blank_formats(app: "AppConfig") -> dict:
    """Стартовые записи formats для нового проекта (по шаблону приложения)."""
    # Замеренные пиксельные размеры юр.блока → scale_pct (п. v0.9)
    _LEGAL_SCALES = {
        "16x9":          {"ad_label_scale_pct": 6.09,  "our_legal_scale_pct": 28.65},
        "4x5":           {"ad_label_scale_pct": 9.26,  "our_legal_scale_pct": 42.59},
        "1x1":           {"ad_label_scale_pct": 7.87,  "our_legal_scale_pct": 36.39},
        "9x16":          {"combined_scale_pct": 22.50, "combined_offset_y_pct": -53.91},
        "byyd_320x480":  {"ad_label_scale_pct": 11.25, "our_legal_scale_pct": 51.56},
        "byyd_480x320":  {"ad_label_scale_pct": 9.17,  "our_legal_scale_pct": 41.67},
        "byyd_768x1024": {"ad_label_scale_pct": 9.11,  "our_legal_scale_pct": 41.67},
        "byyd_1024x768": {"ad_label_scale_pct": 6.84,  "our_legal_scale_pct": 31.25},
        "da_1280x720":   {"ad_label_scale_pct": 6.09,  "our_legal_scale_pct": 28.65},
    }
    out = {}
    for key, fmt in app.formats.items():
        t, d, r, g = _SCALE_DEFAULTS.get(key, (20.0, 12.0, 8.0, 1.5))
        legal = {"show_platform_legal": False, "platform_legal_file": None,
                 "gap_legal_pct": 1.5}
        if fmt.legal_is_vertical:
            legal["show_ad_and_legal_combined"] = True
        else:
            legal["show_ad_label"] = True
            legal["show_our_legal"] = True
        # подставляем замеренные масштабы юр.элементов
        legal.update(_LEGAL_SCALES.get(key, {}))
        out[key] = {
            "region": "RU", "visible": True, "linked": True,
            "settings": {"title_scale_pct": t, "date_scale_pct": d,
                         "rating_scale_pct": r, "gap_title_date_pct": g},
            "legal": legal,
        }
    return out


# ---------------------------------------------------------------------------
# Конфиги приложения (общие для всех проектов)
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    """Загруженные глобальные конфиги приложения."""
    formats: dict[str, FormatDef]
    globals_: GlobalAssets
    data_root: str          # где лежат formats_template.json, safe_zones/...
    global_root: str        # где лежат global_assets/...

    @classmethod
    def load(cls, data_root: str) -> "AppConfig":
        with open(os.path.join(data_root, "formats_template.json"), encoding="utf-8") as f:
            raw = json.load(f)

        # Пересчитываем frame_rect_pct из реального PNG сейф-зоны, если он есть.
        # Это самовосстановление: даже если в JSON закэширован устаревший/
        # неверный прямоугольник ([0,0,1,1] — «вся площадь»), движок всё равно
        # получит корректные поля рамки и не будет ставить текст впритык к краю.
        for key, entry in raw.items():
            sz = entry.get("safe_zone") or {}
            src = sz.get("source_file")
            if not src:
                continue
            png_path = src if os.path.isabs(src) else os.path.join(data_root, src)
            if os.path.exists(png_path):
                try:
                    rect = assets.compute_safe_zone_rect(png_path)
                    sz["frame_rect_pct"] = [round(v, 4) for v in rect]
                except Exception:
                    pass  # битый файл — оставляем закэшированное значение

        formats = {k: FormatDef.from_dict(k, v) for k, v in raw.items()}

        settings_path = os.path.join(data_root, "app_settings.json")
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
        globals_ = GlobalAssets.from_dict(settings)

        return cls(
            formats=formats,
            globals_=globals_,
            data_root=data_root,
            global_root=data_root,
        )


# ---------------------------------------------------------------------------
# Проект (.plshk)
# ---------------------------------------------------------------------------

@dataclass
class Project:
    """Распакованный проект: manifest + путь к распакованным assets/."""
    manifest: dict
    workdir: str                       # временная папка с распакованным .plshk
    path: Optional[str] = None         # путь к самому .plshk на диске

    # --- доступ к элементам по уровням ------------------------------------

    @property
    def movie_title(self) -> str:
        return self.manifest["project_meta"]["movie_title"]

    @property
    def title_element(self) -> Optional[Element]:
        node = self.manifest["elements"].get("title")
        return Element.from_dict(node) if node else None

    def region_date(self, region: str) -> Optional[Element]:
        node = self.manifest["elements"]["by_region"].get(region)
        if not node or not node.get("date_variants"):
            return None
        dv = node["date_variants"]
        active = dv.get("active", "date")
        item = dv.get("items", {}).get(active)
        # «Уже в кино» общий для RU/KZ: если в KZ нет своего now-слота,
        # показываем RU-версию, но обычную KZ-дату не трогаем.
        if item is None and active == "now" and region != "RU":
            ru = self.manifest["elements"]["by_region"].get("RU")
            ru_items = (((ru or {}).get("date_variants") or {}).get("items") or {})
            item = ru_items.get("now")
        return Element.from_dict(item) if item else None

    def region_rating(self, region: str) -> Optional[Element]:
        node = self.manifest["elements"]["by_region"].get(region)
        if not node or not node.get("rating"):
            return None
        return Element.from_dict(node["rating"])

    def project_format(self, key: str) -> ProjectFormat:
        return ProjectFormat.from_dict(key, self.manifest["formats"][key])

    @property
    def format_keys(self) -> list[str]:
        return list(self.manifest["formats"].keys())

    # --- сборка контекста рендера -----------------------------------------

    def build_context(self, app: AppConfig, format_key: str) -> RenderContext:
        pf = self.project_format(format_key)
        fmt = app.formats[format_key]
        region = pf.region if fmt.supports_regions else "RU"
        # п.8: ВО масштабируется регионально — подставляем эффективное значение
        # для активного региона (KZ по умолчанию = 135×135 px). pf здесь —
        # свежесобранный объект, так что правка локальна для этого рендера.
        pf.settings.rating_scale_pct = pf.settings.rating_scale_for(region, fmt.height)
        date_variant = active_status(self, region)
        ctx = RenderContext(
            fmt=fmt,
            pf=pf,
            title=self.title_element,
            date=self.region_date(region),
            rating=self.region_rating(region),
            globals_=app.globals_,
            assets_root=self.workdir,
            global_root=app.global_root,
            project_background=self.manifest.get("project_meta", {}).get("preview_background"),
            date_variant=date_variant,
        )
        # п.3 v0.8: в KZ нет рекламы и юр.инфо — принудительно отключаем ВСЁ
        if region == "KZ":
            ctx.pf.legal.show_ad_label = False
            ctx.pf.legal.show_our_legal = False
            ctx.pf.legal.show_ad_and_legal_combined = False
            ctx.pf.legal.show_platform_legal = False
        return ctx

    def render(self, app: AppConfig, format_key: str, *,
               with_background: bool = False, with_safe_zone: bool = False,
               with_aim: bool = False):
        ctx = self.build_context(app, format_key)
        return render_format(ctx, with_background=with_background,
                             with_safe_zone=with_safe_zone, with_aim=with_aim)

    # --- открыть / сохранить ----------------------------------------------

    @classmethod
    def open(cls, plshk_path: str) -> "Project":
        workdir = tempfile.mkdtemp(prefix="plashker_")
        with zipfile.ZipFile(plshk_path) as z:
            z.extractall(workdir)
        with open(os.path.join(workdir, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        return cls(manifest=manifest, workdir=workdir, path=plshk_path)

    @classmethod
    def open_dir(cls, project_dir: str) -> "Project":
        """Открыть проект из обычной папки (manifest.json + assets/) — удобно
        для разработки и тестов без упаковки в zip."""
        with open(os.path.join(project_dir, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        return cls(manifest=manifest, workdir=project_dir, path=None)

    @classmethod
    def new(cls, title: str, app: "AppConfig") -> "Project":
        """Создать НОВЫЙ пустой проект (без элементов) во временной папке.

        Элементы (title/date/rating) подгружаются позже через import_element,
        когда пользователь перетащит их в Drag&Drop-зоны на стартовом экране.
        Дефолты scale берутся из шаблона форматов приложения.
        """
        workdir = tempfile.mkdtemp(prefix="plashker_new_")
        os.makedirs(os.path.join(workdir, "assets"), exist_ok=True)
        manifest = {
            "version": 1,
            "project_meta": {
                "movie_title": title,
                "project_file_path": "",
                "created_at": _now_iso(),
                "modified_at": _now_iso(),
            },
            "elements": {
                "title": None,
                "by_region": {
                    "RU": {"date_variants": None, "rating": None},
                    "KZ": {"date_variants": None, "rating": None},
                    "BY": {"date_variants": None, "rating": None},
                },
            },
            "active_region": "RU",
            "region_visibility": {"RU": True, "KZ": True, "BY": False},
            "formats": _blank_formats(app),
            "export_templates": [],
        }
        return cls(manifest=manifest, workdir=workdir, path=None)

    # --- готовность проекта (есть ли обязательные элементы) ----------------

    def has_title(self) -> bool:
        return bool(self.manifest["elements"].get("title"))

    def import_asset(self, kind: str, region: str, filename: str,
                     raw_bytes: bytes) -> dict:
        """Сохранить загруженный PNG в assets/ и привязать к manifest.

        kind: title | date | rating. Для title регион игнорируется (общий).
        Возвращает {ok, has_alpha, size} либо {ok: False, error}.
        """
        from .engine import assets as _assets
        os.makedirs(os.path.join(self.workdir, "assets"), exist_ok=True)
        safe_name = os.path.basename(filename) or f"{kind}.png"
        if not safe_name.lower().endswith(".png"):
            safe_name += ".png"
        if kind == "title":
            rel = "assets/title.png"
        else:
            rel = f"assets/{kind}_{region.lower()}_{safe_name}"
        abs_path = os.path.join(self.workdir, rel)
        with open(abs_path, "wb") as f:
            f.write(raw_bytes)

        _assets.clear_cache()
        w, h = _assets.pixel_size(abs_path)
        # п.15 v0.8: для ВО достаточно 100×100, для остальных — 300×100
        min_w = 100 if kind == "rating" else 300
        min_h = 100
        if w < min_w or h < min_h:
            os.remove(abs_path)
            return {"ok": False,
                    "error": f"Минимум {min_w}×{min_h}px, получено {w}×{h}"}
        has_alpha = _assets.has_alpha_channel(abs_path)
        bbox = list(_assets.content_bbox_pct(abs_path))

        axis = "height" if kind == "rating" else "width"
        node = {"file": rel, "has_alpha": has_alpha, "anchor_axis": axis,
                "content_bbox_pct": [round(v, 4) for v in bbox]}

        els = self.manifest["elements"]
        if kind == "title":
            els["title"] = node
        elif kind == "date":
            reg = els["by_region"].setdefault(region, {})
            dv = reg.get("date_variants") or {"active": "date", "items": {}}
            dv.setdefault("items", {})["date"] = node
            # Обычная замена даты не должна затирать вариант «Уже в кино».
            dv["active"] = "date"
            reg["date_variants"] = dv
        elif kind == "rating":
            reg = els["by_region"].setdefault(region, {})
            reg["rating"] = node
        return {"ok": True, "has_alpha": has_alpha, "size": [w, h]}

    def save(self, plshk_path: Optional[str] = None) -> str:
        target = plshk_path or self.path
        if not target:
            raise ValueError("no path to save .plshk")
        self.manifest["project_meta"]["modified_at"] = _now_iso()
        # перезаписываем manifest в workdir, затем пакуем весь workdir
        with open(os.path.join(self.workdir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(self.workdir):
                for name in files:
                    full = os.path.join(root, name)
                    arc = os.path.relpath(full, self.workdir)
                    z.write(full, arc)
        self.path = target
        return target


# ---------------------------------------------------------------------------
# Экспорт (структура папок п.32, имена файлов п.25)
# ---------------------------------------------------------------------------

def export_filename(movie_title: str, region: str, w: int, h: int,
                    status: str) -> str:
    """{movie_title}_{region}_plashka_{w}x{h}-{status}, КРОМЕ RU без региона."""
    region_part = "" if region == "RU" else f"_{region}"
    return f"{movie_title}{region_part}_plashka_{w}x{h}-{status}"


def _format_dir_name(key: str) -> str:
    """Удобочитаемое имя папки формата: 16x9, 9x16, 4x5, 1x1 (через x)."""
    return {"16x9": "16x9", "9x16": "9x16", "4x5": "4x5", "1x1": "1x1"}.get(key, key)


def export_subdir(format_key: str, family: str, region: str) -> str:
    """Относительный путь подпапки экспорта.

    KZ остаётся отдельной папкой, но внутри неё раскладываем по форматам
    через чёрточку/суффикс в UI: KZ/16x9, KZ/9x16 и т.д.
    BYYD складывается в BYYD, а now-версии ниже уходят в BYYD/NOW.
    """
    if region == "KZ":
        return os.path.join("KZ", _format_dir_name(format_key))
    if family == "byyd":
        return "BYYD"
    return _format_dir_name(format_key)


def active_status(project: Project, region: str) -> str:
    """Ключ активного варианта даты (date / now ...), он же {status} в имени."""
    node = project.manifest["elements"]["by_region"].get(region)
    if node and node.get("date_variants"):
        active = node["date_variants"].get("active", "date")
        if active == "now":
            items = node["date_variants"].get("items", {})
            if "now" in items:
                return "now"
            if region != "RU":
                ru = project.manifest["elements"]["by_region"].get("RU")
                ru_items = (((ru or {}).get("date_variants") or {}).get("items") or {})
                if "now" in ru_items:
                    return "now"
        return active if active in node["date_variants"].get("items", {}) else "date"
    return "date"


@dataclass
class ExportOptions:
    formats_include: list[str]
    export_png: bool = True
    export_jpeg_for_approval: bool = False
    export_psd: bool = False
    strip_legal: bool = False
    export_now: bool = True
    export_platform_legal: bool = False
    out_dir: str = "export"


def export(project: Project, app: AppConfig, opts: ExportOptions) -> list[str]:
    """Экспорт (v0.13): каждый формат → PNG clean + full + PSD.

    Если «Уже в кино» загружен — экспортируем обе версии рядом.
    """
    written: list[str] = []
    os.makedirs(opts.out_dir, exist_ok=True)

    parsed_targets = []
    for token in opts.formats_include:
        forced_region = None
        key = token
        if token.endswith("-KZ"):
            key = token[:-3]
            forced_region = "KZ"
        if key in project.manifest["formats"] and project.project_format(key).visible:
            parsed_targets.append((key, forced_region))
    flat = len(parsed_targets) == 1

    # v23: тень — пользовательская визуальная настройка творческих элементов.
    # Для финального экспорта считаем её проектной: если она включена хотя бы в
    # исходном 16×9 или в одном из выбранных форматов, экспортируем все выбранные
    # форматы с той же чёрной тенью. Иначе пользователь видит тень в превью, но
    # часть экспортных форматов может внезапно выйти без неё.
    shadow_source = None
    for shadow_key in ["16x9"] + [k for k, _r in parsed_targets]:
        node = project.manifest.get("formats", {}).get(shadow_key, {})
        st = node.get("settings", {}) or {}
        if st.get("shadow_enabled"):
            shadow_source = {
                "shadow_enabled": True,
                "shadow_blur_pct": float(st.get("shadow_blur_pct", 0.45) or 0.0),
                "shadow_distance_pct": float(st.get("shadow_distance_pct", 0.55) or 0.0),
                "shadow_opacity_pct": float(st.get("shadow_opacity_pct", 55.0) or 0.0),
            }
            break

    def apply_export_shadow(ctx: RenderContext) -> RenderContext:
        if shadow_source:
            for k, v in shadow_source.items():
                setattr(ctx.pf.settings, k, v)
        return ctx

    for key, forced_region in parsed_targets:
        pf = project.project_format(key)
        fmt = app.formats[key]
        old_region = project.manifest["formats"][key].get("region")
        region = forced_region or (pf.region if fmt.supports_regions else "RU")
        if fmt.supports_regions:
            project.manifest["formats"][key]["region"] = region

        base_subdir = opts.out_dir if flat else os.path.join(opts.out_dir, export_subdir(key, fmt.family, region))
        os.makedirs(base_subdir, exist_ok=True)

        # определяем варианты даты для экспорта
        node = project.manifest["elements"]["by_region"].get(region)
        date_variants = ["date"]
        has_now = False
        if node and node.get("date_variants") and "now" in node["date_variants"].get("items", {}):
            has_now = True
        elif region != "RU":
            ru = project.manifest["elements"]["by_region"].get("RU")
            ru_items = (((ru or {}).get("date_variants") or {}).get("items") or {})
            has_now = "now" in ru_items
        if opts.export_now and has_now:
            date_variants.append("now")

        for variant in date_variants:
            # переключить active variant для этого рендера
            if node and node.get("date_variants"):
                node["date_variants"]["active"] = variant

            subdir = base_subdir
            if fmt.family == "byyd" and variant == "now":
                subdir = os.path.join(base_subdir, "NOW")
                os.makedirs(subdir, exist_ok=True)

            base_ctx = apply_export_shadow(project.build_context(app, key))
            platform_modes = [(False, variant)]
            if opts.export_platform_legal and base_ctx.pf.legal.platform_legal_file and region != "KZ":
                platform_modes.append((True, f"{variant}_platform_legal"))

            for with_platform_legal, status in platform_modes:
                ctx_full = apply_export_shadow(project.build_context(app, key))
                ctx_full.pf.legal.show_platform_legal = bool(with_platform_legal)
                if with_platform_legal:
                    if ctx_full.fmt.legal_is_vertical:
                        ctx_full.pf.legal.show_ad_and_legal_combined = True
                    else:
                        ctx_full.pf.legal.show_ad_label = True
                        ctx_full.pf.legal.show_our_legal = True
                fname = export_filename(project.movie_title, region, fmt.width,
                                        fmt.height, status)

                if opts.export_png:
                    img_full = render_format(ctx_full, with_background=False, with_safe_zone=False)
                    full_path = os.path.join(subdir, fname + ".png")
                    img_full.save(full_path)
                    written.append(full_path)

                    ctx_clean = apply_export_shadow(project.build_context(app, key))
                    ctx_clean.pf.legal.show_ad_label = False
                    ctx_clean.pf.legal.show_our_legal = False
                    ctx_clean.pf.legal.show_ad_and_legal_combined = False
                    ctx_clean.pf.legal.show_platform_legal = False
                    img_clean = render_format(ctx_clean, with_background=False, with_safe_zone=False)
                    clean_path = os.path.join(subdir, fname + "_clean.png")
                    img_clean.save(clean_path)
                    written.append(clean_path)

                if opts.export_jpeg_for_approval:
                    approval = render_format(ctx_full, with_background=True, with_safe_zone=False)
                    jpg = approval.convert("RGB")
                    jpg_path = os.path.join(subdir, fname + ".jpg")
                    jpg.save(jpg_path, quality=98, subsampling=0, optimize=True)
                    written.append(jpg_path)

                if opts.export_psd:
                    from .engine.compositor import render_layers
                    from .engine.psd import write_psd
                    img_full_psd = render_format(ctx_full, with_background=False, with_safe_zone=False)
                    layers = render_layers(ctx_full)
                    psd_path = os.path.join(subdir, fname + ".psd")
                    write_psd(psd_path, (fmt.width, fmt.height), layers, img_full_psd)
                    written.append(psd_path)

        # вернуть active variant на "date" после экспорта
        if node and node.get("date_variants") and "date" in node["date_variants"].get("items", {}):
            node["date_variants"]["active"] = "date"
        if fmt.supports_regions:
            project.manifest["formats"][key]["region"] = old_region

    return written


# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "AppConfig",
    "Project",
    "ExportOptions",
    "export",
    "export_filename",
    "export_subdir",
]
