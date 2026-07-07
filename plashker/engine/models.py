"""
Plashker — модели данных движка компоновки.

Три уровня, на которых живут элементы (см. п.3 промпта), здесь явно
разнесены, а не свалены в одну плоскую структуру:

  a) ПРОЕКТНЫЙ   — title (один на весь проект).
  b) РЕГИОНАЛЬНЫЙ — date / rating (свои на каждый регион).
  c) ФОРМАТНЫЙ    — юр.информация площадки (per-format).
  d) ПРИЛОЖЕНЧЕСКИЙ — РЕКЛАМА и наша юр.информация (глобальные ассеты,
                     живут вне .plshk).

Все геометрические величины — в ПРОЦЕНТАХ от размера холста формата
(см. п.1 промпта), пиксели вычисляются движком на лету.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Шаблон форматов (formats_template.json) — общий для всех проектов
# ---------------------------------------------------------------------------

@dataclass
class SafeZone:
    """Единый PNG-файл-рамка формата.

    frame_rect_pct — закэшированный bbox ПРОЗРАЧНОЙ области (инверсия альфы),
    в долях 0..1: [x0, y0, x1, y1]. См. assets.compute_safe_zone_rect.
    """
    source_file: str
    frame_rect_pct: tuple[float, float, float, float]


@dataclass
class FormatDef:
    """Описание одного формата из formats_template.json."""
    key: str
    family: str                      # social | byyd | da
    orientation: str                 # horizontal | vertical
    size_px: tuple[int, int]         # (width, height)
    safe_zone: SafeZone
    legal_mirrored: bool = False     # п.7: РЕКЛАМА и юр.инфо переставлены
    title_mirrored: bool = False     # ВО слева-сверху, название+дата справа-сверху
    supports_regions: bool = True    # п.33: BYYD = False
    # как раскладывать юр.блок (п.5/6):
    #   "auto"       — по ориентации холста (верт. → совмещённый поворот);
    #   "horizontal" — принудительно горизонтально (РЕКЛАМА + наша юр.инфо
    #                  раздельно), даже на вертикальном холсте — для BYYD;
    #   "vertical"   — принудительно совмещённый вертикальный блок.
    legal_layout: str = "auto"
    # якорь совмещённого вертикального блока: "br" (низ-право) | "bl" (низ-лево)
    combined_anchor: str = "br"

    @property
    def width(self) -> int:
        return self.size_px[0]

    @property
    def height(self) -> int:
        return self.size_px[1]

    @property
    def is_vertical(self) -> bool:
        return self.orientation == "vertical"

    @property
    def legal_is_vertical(self) -> bool:
        """Реально ли юр.блок рисуется в вертикальной (совмещённой) раскладке."""
        if self.legal_layout == "horizontal":
            return False
        if self.legal_layout == "vertical":
            return True
        return self.is_vertical

    @classmethod
    def from_dict(cls, key: str, d: dict) -> "FormatDef":
        sz = d["safe_zone"]
        return cls(
            key=key,
            family=d["family"],
            orientation=d["orientation"],
            size_px=tuple(d["size_px"]),  # type: ignore[arg-type]
            safe_zone=SafeZone(
                source_file=sz["source_file"],
                frame_rect_pct=tuple(sz["frame_rect_pct"]),  # type: ignore[arg-type]
            ),
            legal_mirrored=d.get("legal_mirrored", False),
            title_mirrored=d.get("title_mirrored", False),
            supports_regions=d.get("supports_regions", True),
            legal_layout=d.get("legal_layout", "auto"),
            combined_anchor=d.get("combined_anchor", "br"),
        )


# ---------------------------------------------------------------------------
# Элементы плашки (manifest.json) — специфичны проекту
# ---------------------------------------------------------------------------

@dataclass
class Element:
    """Один загруженный PNG-элемент (title / date / rating / legal площадки).

    content_bbox_pct — НЕ деструктивная авто-обрезка: bbox непрозрачного
    контента внутри файла в долях 0..1 (п.15). Сам файл не режется.
    anchor_axis — относительно какой оси холста считается occupancy %.
    """
    file: str
    has_alpha: bool = True
    anchor_axis: str = "width"       # width | height
    content_bbox_pct: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    @classmethod
    def from_dict(cls, d: dict) -> "Element":
        return cls(
            file=d["file"],
            has_alpha=d.get("has_alpha", True),
            anchor_axis=d.get("anchor_axis", "width"),
            content_bbox_pct=tuple(d.get("content_bbox_pct", (0.0, 0.0, 1.0, 1.0))),
        )


# Стартовый размер ВО (рейтинга) для KZ-сегмента: квадратный «ромбик» KZ
# по умолчанию должен рендериться 135×135 px (п.8). Привязка ВО — по высоте,
# поэтому целевое значение задаётся именно в пикселях высоты холста.
KZ_DEFAULT_RATING_PX = 135


@dataclass
class FormatSettings:
    """Процентные настройки одного формата в конкретном проекте."""
    title_scale_pct: float = 18.0
    date_scale_pct: float = 8.0
    rating_scale_pct: float = 10.0
    gap_title_date_pct: float = 2.0
    # п.8: ВО (рейтинг) — региональный элемент со своей геометрией (KZ ромбик
    # крупнее и квадратный, RU — широкий текст). Чтобы один общий процент не
    # делал KZ мизерным, держим переопределения масштаба ВО по регионам.
    # Ключ — регион (KZ/BY/…), значение — occupancy % высоты холста.
    rating_scale_by_region: dict = field(default_factory=dict)
    # п.4: расширенные настройки самой плашки.
    #   show_* — включить/выключить каждый элемент;
    #   swap_title_rating — поменять местами блок «Название+Дата» и «ВО»
    #     (инвертирует штатную зеркальную раскладку формата).
    show_title: bool = True
    show_date: bool = True
    show_rating: bool = True
    swap_title_rating: bool = False
    # Раздельные сдвиги элементов: ключи title/date/date_now/rating,
    # значения {x_pct, y_pct}. Старые offset_x_pct/offset_y_pct читаются как
    # фолбэк для обратной совместимости, но новые правки пишутся сюда.
    element_offsets: dict = field(default_factory=dict)
    # Отдельные параметры варианта «Уже в кино»: он визуально заменяет дату,
    # но не должен наследовать её масштаб/отступ и не должен синхронизироваться
    # вместе с обычной датой.
    now_scale_pct: Optional[float] = None
    now_gap_title_date_pct: Optional[float] = None
    # Полная геометрия режима «Уже в кино»: после первого переключения
    # режим становится самостоятельным и больше не перетирает обычную дату.
    now_title_scale_pct: Optional[float] = None
    now_rating_scale_pct: Optional[float] = None
    # Тень творческих элементов: фиксированно чёрная, управляются только
    # размытие и расстояние от элемента. Значения — в %% от высоты холста.
    shadow_enabled: bool = False
    shadow_blur_pct: float = 0.45
    shadow_distance_pct: float = 0.55
    shadow_opacity_pct: float = 55.0
    export_now_enabled: bool = True
    # legacy v0.14 — оставить для открытия старых .plshk
    offset_x_pct: float = 0.0
    offset_y_pct: float = 0.0

    def rating_scale_for(self, region: str, canvas_h: int) -> float:
        """Эффективный масштаб ВО для региона.

        Если для региона задано переопределение — берём его. Иначе для KZ
        возвращаем стартовое значение, дающее 135×135 px (п.8); для остальных
        регионов — общий rating_scale_pct.
        """
        if region in self.rating_scale_by_region:
            return float(self.rating_scale_by_region[region])
        if region == "KZ" and canvas_h:
            return round(KZ_DEFAULT_RATING_PX / canvas_h * 100.0, 2)
        return self.rating_scale_pct

    @classmethod
    def from_dict(cls, d: dict) -> "FormatSettings":
        base = cls()
        for k in base.__dict__:
            if k in d:
                setattr(base, k, d[k])
        return base


@dataclass
class LegalSettings:
    """Юридический блок формата (п.20).

    Структура зависит от ориентации, но мы храним суперсет полей и читаем
    нужные при рендере.
    """
    # горизонтальные форматы: два раздельных флага
    show_ad_label: bool = False
    show_our_legal: bool = False
    # вертикальные форматы: один совмещённый файл
    show_ad_and_legal_combined: bool = False
    # юр.инфо площадки (per-format, хранится внутри .plshk)
    show_platform_legal: bool = False
    platform_legal_file: Optional[str] = None
    # зазор при совмещении наша+площадки в одной четверти
    gap_legal_pct: float = 1.5
    # occupancy %% юр.элементов (расширение схемы — см. README, п.31)
    ad_label_scale_pct: float = 12.0
    our_legal_scale_pct: float = 18.0
    platform_legal_scale_pct: float = 18.0
    # вертикальный совмещённый блок РЕКЛАМА+юр — это ВЫСОКИЙ узкий файл,
    # его occupancy считается по ВЫСОТЕ холста (а не ширине), иначе он
    # раздувается на весь формат. Дефолт подобран так, чтобы строка
    # уместилась в нижне-правую четверть вертикальных форматов.
    combined_scale_pct: float = 40.0
    # сдвиг вертикального блока по Y в % от высоты холста (отрицательный = вверх)
    combined_offset_y_pct: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "LegalSettings":
        base = cls()
        for k in base.__dict__:
            if k in d:
                setattr(base, k, d[k])
        return base


@dataclass
class ProjectFormat:
    """Запись формата в manifest.formats."""
    key: str
    region: str = "RU"
    visible: bool = True
    linked: bool = True
    settings: FormatSettings = field(default_factory=FormatSettings)
    legal: LegalSettings = field(default_factory=LegalSettings)

    @classmethod
    def from_dict(cls, key: str, d: dict) -> "ProjectFormat":
        return cls(
            key=key,
            region=d.get("region", "RU"),
            visible=d.get("visible", True),
            linked=d.get("linked", True),
            settings=FormatSettings.from_dict(d.get("settings", {})),
            legal=LegalSettings.from_dict(d.get("legal", {})),
        )


# ---------------------------------------------------------------------------
# Глобальные ассеты приложения (app_settings.json) — вне .plshk
# ---------------------------------------------------------------------------

@dataclass
class GlobalAssets:
    """РЕКЛАМА / наша юр.информация / декоративный фон (п.2)."""
    ad_label_h: Optional[str] = None          # горизонтальная РЕКЛАМА
    our_legal_h: Optional[str] = None         # горизонтальная наша юр.инфо
    ad_and_legal_combined_v: Optional[str] = None  # вертикальный совмещённый
    preview_background: Optional[str] = None
    preview_bg_fit: str = "cover"

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalAssets":
        h = d.get("horizontal", {})
        v = d.get("vertical", {})
        bg = d.get("preview_background", {})
        def f(node):
            return node.get("file") if node else None
        return cls(
            ad_label_h=f(h.get("ad_label")),
            our_legal_h=f(h.get("our_legal")),
            ad_and_legal_combined_v=f(v.get("ad_and_legal_combined")),
            preview_background=bg.get("file"),
            preview_bg_fit=bg.get("fit_mode", "cover"),
        )
