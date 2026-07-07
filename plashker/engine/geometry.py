"""
Plashker — геометрия раскладки (полностью автоматическая, п.7).

Никаких X/Y-контролов: позиция КАЖДОГО элемента вычисляется здесь из
safe-zone рамки и значений Scale/Gap. Пользователь управляет только
масштабом и зазором.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """Прямоугольник в пикселях холста."""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Quarters:
    """Четыре четверти safe-zone рамки по центральным линиям (п.7)."""
    top_left: Rect       # title
    top_right: Rect      # rating
    bottom_left: Rect    # ad_label   (или legal при legal_mirrored)
    bottom_right: Rect   # our/platform legal (или ad_label при legal_mirrored)


def frame_rect_px(frame_rect_pct, canvas_w: int, canvas_h: int) -> Rect:
    """Перевести frame_rect_pct (доли) в пиксельный прямоугольник холста."""
    x0, y0, x1, y1 = frame_rect_pct
    return Rect(x0 * canvas_w, y0 * canvas_h, x1 * canvas_w, y1 * canvas_h)


def split_quarters(frame: Rect) -> Quarters:
    """Поделить рамку на 4 четверти по центральным линиям.

    ⚠️ Деление на четверти — интерпретация автора спеки (раздел 0, п.7),
    помеченная как «подтвердить с пользователем». Реализовано буквально по
    описанию; если ожидание иное — меняется только эта функция.
    """
    cx, cy = frame.cx, frame.cy
    return Quarters(
        top_left=Rect(frame.x0, frame.y0, cx, cy),
        top_right=Rect(cx, frame.y0, frame.x1, cy),
        bottom_left=Rect(frame.x0, cy, cx, frame.y1),
        bottom_right=Rect(cx, cy, frame.x1, frame.y1),
    )


def occupancy_scale_factor(
    content_w: float,
    content_h: float,
    scale_pct: float,
    anchor_axis: str,
    canvas_w: int,
    canvas_h: int,
) -> float:
    """Коэффициент пропорционального масштаба под целевой occupancy % (п.15).

    scale_pct = «видимый контент занимает N% от заданной оси холста».
    Множитель применяется к ОБЕИМ осям одинаково — искажение запрещено.
    """
    if content_w <= 0 or content_h <= 0:
        return 1.0
    if anchor_axis == "height":
        target = scale_pct / 100.0 * canvas_h
        return target / content_h
    # по умолчанию — по ширине
    target = scale_pct / 100.0 * canvas_w
    return target / content_w


def anchor_in_quarter(
    quarter: Rect,
    elem_w: float,
    elem_h: float,
    corner: str,
) -> tuple[float, float]:
    """Прижать элемент углом к углу его четверти. Возвращает (x, y) пасты.

    corner: tl | tr | bl | br — какой угол четверти является «якорем».
    """
    if corner == "tl":
        return quarter.x0, quarter.y0
    if corner == "tr":
        return quarter.x1 - elem_w, quarter.y0
    if corner == "bl":
        return quarter.x0, quarter.y1 - elem_h
    if corner == "br":
        return quarter.x1 - elem_w, quarter.y1 - elem_h
    raise ValueError(f"unknown corner {corner!r}")


def fit_factor_to_quarter(
    content_w: float,
    content_h: float,
    quarter: Rect,
) -> float:
    """Максимальный множитель, при котором контент ещё влезает в четверть.

    Используется для ограничения Scale сверху размером четверти (п.7) —
    гарантия отсутствия перекрытий не предупреждением, а математикой.
    """
    if content_w <= 0 or content_h <= 0:
        return 1.0
    return min(quarter.w / content_w, quarter.h / content_h)


def scale_bounds_pct(
    default_pct: float,
    content_w: float,
    content_h: float,
    quarter: Rect,
    anchor_axis: str,
    canvas_w: int,
    canvas_h: int,
    low_mult: float = 0.80,
    floor_pct: float = 0.0,
    high_mult: float = 1.40,
) -> tuple[float, float]:
    """Границы ползунка Scale для элемента (п.31).

    Нижняя = default * low_mult (но не ниже floor_pct), верхняя =
    min(default * high_mult, предел четверти).
    """
    low = max(floor_pct, default_pct * low_mult)
    high_user = default_pct * high_mult

    # во что превращается «упереться в четверть» в терминах occupancy %
    fit_mult = fit_factor_to_quarter(content_w, content_h, quarter)
    if anchor_axis == "height":
        high_quarter = (content_h * fit_mult) / canvas_h * 100.0
    else:
        high_quarter = (content_w * fit_mult) / canvas_w * 100.0

    high = min(high_user, high_quarter)
    if high < low:
        high = low
    return (round(low, 2), round(high, 2))
