"""
Plashker — движок компоновки (compositor).

Это ядро первого этапа (см. конец промпта): читает manifest / formats-конфиг
/ глобальные ассеты → накладывает элементы на холст по ПРОЦЕНТНЫМ правилам,
включая юридический блок → отдаёт PNG.

Порядок раскладки на холсте (snapshot, п.7):
  ┌───────────────┬───────────────┐
  │ title (TL)    │     rating (TR)│
  │   └ date под  │                │
  │     title     │                │
  ├───────────────┼───────────────┤
  │ РЕКЛАМА (BL)  │ юр.инфо (BR)   │   ← legal_mirrored меняет BL/BR местами
  └───────────────┴───────────────┘
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageFilter

from . import assets, geometry as geo
from .models import (
    Element,
    FormatDef,
    GlobalAssets,
    LegalSettings,
    ProjectFormat,
)


@dataclass
class RenderContext:
    """Всё, что нужно движку, чтобы отрисовать ОДИН формат для ОДНОГО региона."""
    fmt: FormatDef                  # из formats_template.json
    pf: ProjectFormat               # из manifest.formats[key]
    title: Element                  # проектный уровень
    date: Optional[Element]         # региональный
    rating: Optional[Element]       # региональный
    globals_: GlobalAssets          # приложенческий уровень
    assets_root: str                # корень, относительно которого file-пути
    global_root: str                # корень глобальных ассетов
    project_background: Optional[str] = None  # п.7 v0.5.1: пользовательский фон
    date_variant: str = "date"              # date | now


def _resolve(root: str, rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


def _content_size(path: str) -> tuple[float, float]:
    """Размер видимого контента файла в пикселях (после авто-кропа по альфе)."""
    img = assets.load_rgba(path)
    x0p, y0p, x1p, y1p = assets.content_bbox_pct(path)
    w, h = img.size
    return ((x1p - x0p) * w, (y1p - y0p) * h)


def _prepare_element(path: str, scale_pct: float, anchor_axis: str,
                     canvas_w: int, canvas_h: int) -> Image.Image:
    """Загрузить → обрезать по content bbox → масштабировать под occupancy %.

    Возвращает уже отмасштабированный КОНТЕНТ (без прозрачных полей файла).
    """
    img = assets.load_rgba(path)
    x0p, y0p, x1p, y1p = assets.content_bbox_pct(path)
    w, h = img.size
    box = (round(x0p * w), round(y0p * h), round(x1p * w), round(y1p * h))
    content = img.crop(box)
    cw, ch = content.size

    factor = geo.occupancy_scale_factor(cw, ch, scale_pct, anchor_axis,
                                        canvas_w, canvas_h)
    new_w = max(1, round(cw * factor))
    new_h = max(1, round(ch * factor))
    return content.resize((new_w, new_h), Image.LANCZOS)


def _paste(canvas: Image.Image, elem: Image.Image, xy: tuple[float, float]) -> None:
    x, y = round(xy[0]), round(xy[1])
    canvas.alpha_composite(elem, (x, y))


def _paste_shadow(canvas: Image.Image, elem: Image.Image, xy: tuple[float, float], ctx: RenderContext) -> None:
    """Чёрная тень под творческими элементами.

    Параметры хранятся в процентах от высоты холста, чтобы одинаково вести
    себя на 16:9, 9:16 и маленьких BYYD-форматах.
    """
    s = ctx.pf.settings
    if not getattr(s, "shadow_enabled", False):
        return
    W, H = ctx.fmt.width, ctx.fmt.height
    blur = max(0.0, float(getattr(s, "shadow_blur_pct", 0.45) or 0.0) / 100.0 * H)
    dist = float(getattr(s, "shadow_distance_pct", 0.55) or 0.0) / 100.0 * H
    opacity = max(0.0, min(100.0, float(getattr(s, "shadow_opacity_pct", 55.0) or 0.0))) / 100.0
    alpha = elem.getchannel("A").point(lambda a: int(a * opacity))
    shadow = Image.new("RGBA", elem.size, (0, 0, 0, 255))
    shadow.putalpha(alpha)
    if blur > 0:
        pad = max(2, int(round(blur * 3)))
        padded = Image.new("RGBA", (elem.width + pad * 2, elem.height + pad * 2), (0, 0, 0, 0))
        padded.alpha_composite(shadow, (pad, pad))
        shadow = padded.filter(ImageFilter.GaussianBlur(radius=blur))
        sx = xy[0] + dist - pad
        sy = xy[1] + dist - pad
    else:
        sx = xy[0] + dist
        sy = xy[1] + dist
    _paste(canvas, shadow, (sx, sy))


def _paste_with_shadow(canvas: Image.Image, elem: Image.Image, xy: tuple[float, float], ctx: RenderContext) -> None:
    _paste_shadow(canvas, elem, xy, ctx)
    _paste(canvas, elem, xy)


def _active_date_key(ctx: RenderContext) -> str:
    return "date_now" if getattr(ctx, "date_variant", "date") == "now" else "date"


def _date_scale(ctx: RenderContext) -> float:
    s = ctx.pf.settings
    if _active_date_key(ctx) == "date_now" and getattr(s, "now_scale_pct", None) is not None:
        return float(s.now_scale_pct)
    return float(s.date_scale_pct)


def _date_gap(ctx: RenderContext) -> float:
    s = ctx.pf.settings
    if _active_date_key(ctx) == "date_now" and getattr(s, "now_gap_title_date_pct", None) is not None:
        return float(s.now_gap_title_date_pct)
    return float(s.gap_title_date_pct)


def _title_scale(ctx: RenderContext) -> float:
    s = ctx.pf.settings
    if _active_date_key(ctx) == "date_now" and getattr(s, "now_title_scale_pct", None) is not None:
        return float(s.now_title_scale_pct)
    return float(s.title_scale_pct)


def _rating_scale(ctx: RenderContext) -> float:
    s = ctx.pf.settings
    if _active_date_key(ctx) == "date_now" and getattr(s, "now_rating_scale_pct", None) is not None:
        return float(s.now_rating_scale_pct)
    return float(s.rating_scale_pct)


def _element_offset(ctx: RenderContext, key: str) -> tuple[float, float]:
    s = ctx.pf.settings
    offsets = getattr(s, "element_offsets", None) or {}
    node = offsets.get(key) or {}
    # Для старых проектов, где был общий offset, применяем его как фолбэк ко всем
    # творческим элементам. Как только UI пишет element_offsets, элементы становятся
    # независимыми.
    x = node.get("x_pct", getattr(s, "offset_x_pct", 0.0))
    y = node.get("y_pct", getattr(s, "offset_y_pct", 0.0))
    return (float(x or 0.0) / 100.0 * ctx.fmt.width,
            float(y or 0.0) / 100.0 * ctx.fmt.height)


def render_format(ctx: RenderContext, *,
                  with_background: bool = False,
                  with_safe_zone: bool = False,
                  with_aim: bool = False) -> Image.Image:
    """Отрисовать формат и вернуть готовое RGBA-изображение плашки.

    with_background / with_safe_zone — ТОЛЬКО для превью (п.280): на
    основной экспорт PNG/PSD они не влияют никогда (вызывается с False).
    """
    W, H = ctx.fmt.width, ctx.fmt.height
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # --- 0. опциональный декоративный фон (только превью) -----------------
    if with_background:
        bg_path = None
        # проектный фон приоритетнее глобального (п.7 v0.5.1)
        if ctx.project_background:
            bg_path = _resolve(ctx.assets_root, ctx.project_background)
        if (not bg_path or not os.path.exists(bg_path or "")) and ctx.globals_.preview_background:
            bg_path = _resolve(ctx.global_root, ctx.globals_.preview_background)
        if bg_path and os.path.exists(bg_path):
            canvas.alpha_composite(_fit_cover(assets.load_rgba(bg_path), W, H))

    # --- геометрия рамки и четвертей --------------------------------------
    frame = geo.frame_rect_px(ctx.fmt.safe_zone.frame_rect_pct, W, H)
    q = geo.split_quarters(frame)

    # зеркальная раскладка (п.8): для части форматов ВО — слева-сверху,
    # а название с датой — справа-сверху. swap_title_rating (п.4) инвертирует
    # штатную раскладку формата по желанию пользователя.
    s = ctx.pf.settings
    effective_mirror = ctx.fmt.title_mirrored ^ bool(getattr(s, "swap_title_rating", False))
    if effective_mirror:
        title_q, title_corner = q.top_right, "tr"
        rating_q, rating_corner = q.top_left, "tl"
    else:
        title_q, title_corner = q.top_left, "tl"
        rating_q, rating_corner = q.top_right, "tr"

    date_key = _active_date_key(ctx)

    # --- 1–2. TITLE + DATE как единый верхний блок -------------------------
    # Блок выравнивается по своей четверти safe-zone. Если дата шире названия,
    # название центрируется относительно даты; если название шире — дата
    # центрируется относительно названия. Так пользователь может сделать дату
    # крупнее title без вылета за рамки и без поломки взаимной центровки.
    # title может быть None, если пользователь удалил исходник через
    # «Заменить…» (remove_element("title")) и ещё не загрузил новый —
    # раньше .title.file падал тут с AttributeError на каждый рендер.
    title_img = None
    if ctx.title is not None:
        title_path = _resolve(ctx.assets_root, ctx.title.file)
        title_img = _prepare_element(title_path, _title_scale(ctx),
                                     ctx.title.anchor_axis, W, H)
    date_img = None
    if ctx.date is not None and getattr(s, "show_date", True):
        date_path = _resolve(ctx.assets_root, ctx.date.file)
        date_img = _prepare_element(date_path, _date_scale(ctx),
                                    ctx.date.anchor_axis, W, H)

    title_w = title_img.width if title_img is not None else 0
    title_h = title_img.height if title_img is not None else 0
    block_w = max(title_w, date_img.width if date_img is not None else 0)
    if title_corner == "tr":
        block_x = title_q.x1 - block_w
    else:
        block_x = title_q.x0
    tx = block_x + (block_w - title_w) / 2
    ty = title_q.y0

    if title_img is not None and getattr(s, "show_title", True):
        tox, toy = _element_offset(ctx, "title")
        _paste_with_shadow(canvas, title_img, (tx + tox, ty + toy), ctx)

    if date_img is not None:
        gap_px = _date_gap(ctx) / 100.0 * H
        dx = block_x + (block_w - date_img.width) / 2
        dy = ty + title_h + gap_px
        dox, doy = _element_offset(ctx, date_key)
        _paste_with_shadow(canvas, date_img, (dx + dox, dy + doy), ctx)

    # --- 3. RATING -> своя верхняя четверть --------------------------------
    if ctx.rating is not None and getattr(s, "show_rating", True):
        rating_path = _resolve(ctx.assets_root, ctx.rating.file)
        rating_img = _prepare_element(rating_path, _rating_scale(ctx),
                                      ctx.rating.anchor_axis, W, H)
        rx, ry = geo.anchor_in_quarter(rating_q, rating_img.width,
                                       rating_img.height, rating_corner)
        rox, roy = _element_offset(ctx, "rating")
        _paste_with_shadow(canvas, rating_img, (rx + rox, ry + roy), ctx)

    # --- 4. ЮРИДИЧЕСКИЙ БЛОК ----------------------------------------------
    _render_legal(canvas, ctx, q)

    # --- 5. оверлей safe zone (только превью) -----------------------------
    if with_safe_zone or with_aim:
        _draw_safe_zone_overlay(canvas, frame, q, ctx, draw_safe=with_safe_zone, draw_aim=with_aim)

    return canvas


# ---------------------------------------------------------------------------
# Юридический блок (п.20): структура зависит от ориентации формата
# ---------------------------------------------------------------------------

def _render_legal(canvas: Image.Image, ctx: RenderContext, q: geo.Quarters,
                  *, draw_ad: bool = True, draw_legal: bool = True) -> None:
    """Отрисовать юр.блок.

    draw_ad / draw_legal (п.1 v0.7) позволяют рисовать ТОЛЬКО «РЕКЛАМА» или
    ТОЛЬКО юр.информацию — нужно для PSD, где это два РАЗНЫХ слоя. Геометрия
    (в т.ч. подгон высоты юр.инфо под «РЕКЛАМА», п.5) считается одинаково
    независимо от того, что именно рисуем, — позиции слоёв совпадают со
    сведённым кадром.
    """
    legal: LegalSettings = ctx.pf.legal
    W, H = ctx.fmt.width, ctx.fmt.height
    g = ctx.globals_

    # с учётом legal_mirrored меняем местами нижние четверти (п.7)
    if ctx.fmt.legal_mirrored:
        ad_quarter, ad_corner = q.bottom_right, "br"
        legal_quarter, legal_corner = q.bottom_left, "bl"
    else:
        ad_quarter, ad_corner = q.bottom_left, "bl"
        legal_quarter, legal_corner = q.bottom_right, "br"

    platform_path = _resolve(ctx.assets_root, legal.platform_legal_file)

    if ctx.fmt.legal_is_vertical:
        # ----- ВЕРТИКАЛЬ: РЕКЛАМА+юр.инфо = ОДИН совмещённый файл ----------
        # ВАЖНО (п.6): это ВЫСОКИЙ узкий файл (повёрнутый текст), occupancy
        # считаем по ВЫСОТЕ, иначе блок раздувается на весь формат и юр.инфа
        # уезжает за пределы холста.
        #
        # combined_anchor (п.6) выбирает, к какой нижней стороне прижать блок:
        #   "br" — низ-право (по умолчанию), "bl" — низ-лево.
        if ctx.fmt.combined_anchor == "bl":
            comb_quarter, comb_corner = ad_quarter, ad_corner
        else:
            comb_quarter, comb_corner = legal_quarter, legal_corner
        if legal.show_ad_and_legal_combined and g.ad_and_legal_combined_v:
            combined_path = _resolve(ctx.global_root, g.ad_and_legal_combined_v)
            combined = _prepare_element(combined_path, legal.combined_scale_pct,
                                        "height", W, H)
            if legal.show_platform_legal and platform_path:
                # площадка в основном слоте, наша СЛЕВА от неё (gap по ширине)
                plat = _prepare_element(platform_path,
                                        combined.height / H * 100.0, "height", W, H)
                px, py = geo.anchor_in_quarter(comb_quarter, plat.width,
                                               plat.height, comb_corner)
                if draw_legal:
                    _paste(canvas, plat, (px, py))
                gap_px = legal.gap_legal_pct / 100.0 * W
                cx = px - gap_px - combined.width
                cy = comb_quarter.y1 - combined.height
                if draw_legal:
                    _paste(canvas, combined, (cx, cy))
            else:
                # Anchor = top-right (п. v0.12): X прижат вправо, Y задаётся offset'ом
                # от верха, при изменении scale верхняя точка не двигается
                cx = comb_quarter.x1 - combined.width
                if legal.combined_offset_y_pct:
                    # offset задаёт позицию ВЕРХА элемента (отрицательный = вверх от низа)
                    base_bottom = comb_quarter.y1
                    top_y = base_bottom + (legal.combined_offset_y_pct / 100.0 * H)
                    cy = max(0, min(top_y, H - combined.height))
                else:
                    cy = comb_quarter.y1 - combined.height
                if draw_legal:
                    _paste(canvas, combined, (cx, cy))
        elif legal.show_platform_legal and platform_path:
            # Совмещённый ad+legal не показан (выключен/нет ассета) — площадка
            # стоит одна, своим собственным процентом (был баг: тут читали
            # `combined.height`, а `combined` в этой ветке не создаётся вовсе —
            # NameError на каждом таком экспорте).
            plat = _prepare_element(platform_path, legal.platform_legal_scale_pct,
                                    "height", W, H)
            px, py = geo.anchor_in_quarter(comb_quarter, plat.width,
                                           plat.height, comb_corner)
            if draw_legal:
                _paste(canvas, plat, (px, py))
        return

    # ----- ГОРИЗОНТАЛЬ: два раздельных элемента ----------------------------
    # Фолбэк (п.5): если у формата стоит только «совмещённый» флаг (как у
    # старых вертикальных BYYD-записей), но раскладка теперь горизонтальная —
    # трактуем его и как РЕКЛАМУ, и как нашу юр.инфо, чтобы ничего не пропало.
    show_ad = legal.show_ad_label or legal.show_ad_and_legal_combined
    show_our = legal.show_our_legal or legal.show_ad_and_legal_combined

    # РЕКЛАМА -> своя нижняя четверть
    ad = None
    if show_ad and g.ad_label_h:
        ad_path = _resolve(ctx.global_root, g.ad_label_h)
        ad = _prepare_element(ad_path, legal.ad_label_scale_pct, "width", W, H)
        ax, ay = geo.anchor_in_quarter(ad_quarter, ad.width, ad.height, ad_corner)
        if draw_ad:
            _paste(canvas, ad, (ax, ay))

    # наша юр.инфо и/или площадка -> нижне-правая (в норме) четверть
    our = None
    if show_our and g.our_legal_h:
        our_path = _resolve(ctx.global_root, g.our_legal_h)
        # Юр.инфо ВСЕГДА подгоняется по высоте к РЕКЛАМА — даже если РЕКЛАМА
        # скрыта, размер юр.инфо не должен меняться (п. v0.11)
        ad_ref_h = None
        if ad is not None:
            ad_ref_h = float(ad.height)
        elif g.ad_label_h:
            # посчитать высоту РЕКЛАМЫ виртуально
            ad_path_ref = _resolve(ctx.global_root, g.ad_label_h)
            if ad_path_ref:
                ad_ref = _prepare_element(ad_path_ref, legal.ad_label_scale_pct, "width", W, H)
                ad_ref_h = float(ad_ref.height)
        if ad_ref_h is not None:
            cw, ch = _content_size(our_path)
            aspect = (cw / ch) if ch else 1.0
            max_h_by_quarter = (legal_quarter.w / aspect) if aspect else ad_ref_h
            final_h = max(1.0, min(ad_ref_h, max_h_by_quarter))
            our = _prepare_element(our_path, final_h / H * 100.0, "height", W, H)
        else:
            our = _prepare_element(our_path, legal.our_legal_scale_pct, "width", W, H)

    plat = None
    if legal.show_platform_legal and platform_path:
        if our is not None:
            plat = _prepare_element(platform_path, our.height / H * 100.0, "height", W, H)
        else:
            plat = _prepare_element(platform_path, legal.platform_legal_scale_pct,
                                    "width", W, H)

    if plat is not None and our is not None:
        # НАША юр.инфо — в углу (ниже), юр.инфо ПЛОЩАДКИ — НАД ней (п.6).
        ox, oy = geo.anchor_in_quarter(legal_quarter, our.width, our.height,
                                       legal_corner)
        if draw_legal:
            _paste(canvas, our, (ox, oy))
        gap_px = legal.gap_legal_pct / 100.0 * H
        py = oy - gap_px - plat.height
        px = legal_quarter.x1 - plat.width
        if draw_legal:
            _paste(canvas, plat, (px, py))
    elif plat is not None:
        px, py = geo.anchor_in_quarter(legal_quarter, plat.width, plat.height,
                                       legal_corner)
        if draw_legal:
            _paste(canvas, plat, (px, py))
    elif our is not None:
        ox, oy = geo.anchor_in_quarter(legal_quarter, our.width, our.height,
                                       legal_corner)
        if draw_legal:
            _paste(canvas, our, (ox, oy))


# ---------------------------------------------------------------------------
# Послойный рендер для PSD-экспорта (п.10): 4 раздельных слоя на холсте
# ---------------------------------------------------------------------------

def render_layers(ctx: RenderContext) -> list[tuple[str, Image.Image]]:
    """Вернуть упорядоченные слои [(имя, RGBA на весь холст), ...].

    Слои (снизу вверх в PSD будут перевёрнуты writer-ом): Тайтл, Дата, ВО,
    РЕКЛАМА, Юр.информация. «РЕКЛАМА» и юр.информация — РАЗНЫЕ слои (п.1).
    Геометрия идентична render_format (учитывает swap/видимость, п.4), но
    каждый элемент — на своём прозрачном холсте для независимой правки.
    """
    W, H = ctx.fmt.width, ctx.fmt.height
    s = ctx.pf.settings
    frame = geo.frame_rect_px(ctx.fmt.safe_zone.frame_rect_pct, W, H)
    q = geo.split_quarters(frame)

    effective_mirror = ctx.fmt.title_mirrored ^ bool(getattr(s, "swap_title_rating", False))
    if effective_mirror:
        title_q, title_corner = q.top_right, "tr"
        rating_q, rating_corner = q.top_left, "tl"
    else:
        title_q, title_corner = q.top_left, "tl"
        rating_q, rating_corner = q.top_right, "tr"

    date_key = _active_date_key(ctx)

    def blank() -> Image.Image:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    layers: list[tuple[str, Image.Image]] = []

    # 1–2. Title + Date: та же блочная геометрия, что в render_format().
    title_layer = blank()
    title_path = _resolve(ctx.assets_root, ctx.title.file)
    title_img = _prepare_element(title_path, _title_scale(ctx),
                                 ctx.title.anchor_axis, W, H)
    date_img = None
    if ctx.date is not None and getattr(s, "show_date", True):
        date_path = _resolve(ctx.assets_root, ctx.date.file)
        date_img = _prepare_element(date_path, _date_scale(ctx),
                                    ctx.date.anchor_axis, W, H)
    block_w = max(title_img.width, date_img.width if date_img is not None else 0)
    block_x = title_q.x1 - block_w if title_corner == "tr" else title_q.x0
    tx = block_x + (block_w - title_img.width) / 2
    ty = title_q.y0
    if getattr(s, "show_title", True):
        tox, toy = _element_offset(ctx, "title")
        _paste_with_shadow(title_layer, title_img, (tx + tox, ty + toy), ctx)
    layers.append(("Title", title_layer))

    date_layer = blank()
    if date_img is not None:
        gap_px = _date_gap(ctx) / 100.0 * H
        dx = block_x + (block_w - date_img.width) / 2
        dy = ty + title_img.height + gap_px
        dox, doy = _element_offset(ctx, date_key)
        _paste_with_shadow(date_layer, date_img, (dx + dox, dy + doy), ctx)
    layers.append(("Date", date_layer))

    # 3. ВО (рейтинг)
    rating_layer = blank()
    if ctx.rating is not None and getattr(s, "show_rating", True):
        rating_path = _resolve(ctx.assets_root, ctx.rating.file)
        rating_img = _prepare_element(rating_path, _rating_scale(ctx),
                                      ctx.rating.anchor_axis, W, H)
        rx, ry = geo.anchor_in_quarter(rating_q, rating_img.width,
                                       rating_img.height, rating_corner)
        rox, roy = _element_offset(ctx, "rating")
        _paste_with_shadow(rating_layer, rating_img, (rx + rox, ry + roy), ctx)
    layers.append(("VO (rating)", rating_layer))

    # 4. РЕКЛАМА — отдельный слой (п.1)
    ad_layer = blank()
    _render_legal(ad_layer, ctx, q, draw_ad=True, draw_legal=False)
    layers.append(("REKLAMA", ad_layer))

    # 5. Юр.информация — отдельный слой (п.1)
    legal_layer = blank()
    _render_legal(legal_layer, ctx, q, draw_ad=False, draw_legal=True)
    layers.append(("Legal info", legal_layer))

    return layers


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Масштаб «cover»: заполнить кадр без пустот, обрезать лишнее (п.2)."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    nw, nh = round(src_w * scale), round(src_h * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


_SAFE_OVERLAY_CACHE = {}


def _draw_safe_zone_overlay(canvas: Image.Image, frame: geo.Rect,
                            q: geo.Quarters, ctx: "RenderContext" = None,
                            *, draw_safe: bool = True, draw_aim: bool = False) -> None:
    """Быстрый overlay safe-zone. Прицел рисуется только отдельной кнопкой."""
    from PIL import ImageDraw
    W, H = canvas.size
    if draw_safe and ctx and ctx.fmt.safe_zone.source_file:
        try:
            import os
            sz_path = ctx.fmt.safe_zone.source_file
            if not os.path.isabs(sz_path):
                sz_path = os.path.join(ctx.global_root, sz_path)
            if os.path.exists(sz_path):
                key = (sz_path, W, H, os.path.getmtime(sz_path))
                sz_img = _SAFE_OVERLAY_CACHE.get(key)
                if sz_img is None:
                    sz_img = assets.load_rgba(sz_path)
                    if sz_img.size != (W, H):
                        sz_img = sz_img.resize((W, H), Image.BILINEAR)
                    _SAFE_OVERLAY_CACHE.clear()
                    _SAFE_OVERLAY_CACHE[key] = sz_img
                canvas.alpha_composite(sz_img)
        except Exception:
            pass
    if draw_aim:
        draw = ImageDraw.Draw(canvas)
        line = (242, 165, 60, 200)
        draw.rectangle([frame.x0, frame.y0, frame.x1, frame.y1], outline=line, width=3)
        draw.line([frame.cx, frame.y0, frame.cx, frame.y1], fill=line, width=2)
        draw.line([frame.x0, frame.cy, frame.x1, frame.cy], fill=line, width=2)


__all__ = ["RenderContext", "render_format", "render_layers"]
