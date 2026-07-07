"""
Plashker — загрузка изображений и анализ альфа-канала.

Здесь живут две ЗЕРКАЛЬНЫЕ друг другу операции, которые легко перепутать
(см. п.6 промпта):

1. content_bbox для ОБЫЧНЫХ элементов (title/date/rating/legal) —
   bbox НЕПРОЗРАЧНОГО контента, как обычный Image.getbbox(). Нужен для
   корректного расчёта occupancy %: без него % считался бы от файла с
   пустыми прозрачными полями (п.15).

2. frame_rect для SAFE ZONE — bbox ПРОЗРАЧНОЙ «дырки», то есть ИНВЕРСИЯ
   относительно обычного getbbox(). Файл-рамка полностью залит, прозрачна
   только безопасная зона в середине; её и надо найти.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageChops, ImageOps


@lru_cache(maxsize=256)
def load_rgba(path: str) -> Image.Image:
    """Открыть PNG как RGBA (с кэшем — один файл читается с диска один раз)."""
    return Image.open(path).convert("RGBA")


def content_bbox_pct(path: str) -> tuple[float, float, float, float]:
    """bbox НЕПРОЗРАЧНОГО контента в долях 0..1 от размера файла.

    Не деструктивно: исходный PNG не меняется, возвращаются только доли,
    которые кэшируются в manifest как content_bbox_pct (п.15).
    Если у файла нет альфы или контент заполняет весь кадр — вернётся
    (0, 0, 1, 1).
    """
    img = load_rgba(path)
    w, h = img.size
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()  # bbox непрозрачных пикселей
    if bbox is None:                       # полностью прозрачный файл
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = bbox
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def compute_safe_zone_rect(path: str,
                           transparent_threshold: int = 16
                           ) -> tuple[float, float, float, float]:
    """frame_rect_pct: bbox ПРОЗРАЧНОЙ «дырки» рамки safe zone (п.6).

    Нам нужен bbox именно ПРОЗРАЧНЫХ пикселей (сама безопасная зона), а
    залитая рамка-поле — за его пределами. Берём маску, где «видимыми» для
    getbbox() считаются только достаточно прозрачные пиксели (alpha ниже
    порога), и находим её границы.

    ВАЖНО: простой `ImageChops.invert(alpha)` здесь НЕ работает, если рамка
    нарисована полупрозрачным цветом (alpha рамки < 255): после инверсии её
    пиксели всё равно ненулевые, и getbbox() возвращает весь холст. Поэтому
    используем явный порог прозрачности, а не инверсию.

    Пересчитывается автоматически при замене source_file — координаты
    никогда не вводятся вручную.
    """
    img = load_rgba(path)
    w, h = img.size
    alpha = img.getchannel("A")
    # маска прозрачной зоны: 255 там, где пиксель достаточно прозрачен
    hole = alpha.point(lambda a: 255 if a < transparent_threshold else 0)
    bbox = hole.getbbox()
    if bbox is None:                       # прозрачной дырки нет -> вся площадь
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = bbox
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def has_alpha_channel(path: str) -> bool:
    """Есть ли у PNG непустой альфа-канал (п.13 — валидация без блокировки)."""
    img = Image.open(path)
    if "A" not in img.getbands():
        return False
    # бывает формальный, но полностью непрозрачный альфа-канал
    extrema = img.convert("RGBA").getchannel("A").getextrema()
    return extrema[0] < 255


def pixel_size(path: str) -> tuple[int, int]:
    """Размер файла в пикселях (для проверки минимума 300x100, п.29)."""
    with Image.open(path) as im:
        return im.size


def clear_cache() -> None:
    """Сбросить кэш загрузки (после замены файла на диске)."""
    load_rgba.cache_clear()


__all__ = [
    "load_rgba",
    "content_bbox_pct",
    "compute_safe_zone_rect",
    "has_alpha_channel",
    "pixel_size",
    "clear_cache",
    "Image",
    "ImageOps",
]
