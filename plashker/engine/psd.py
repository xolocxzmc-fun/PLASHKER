"""
Plashker — минимальный writer PSD (Photoshop) для экспорта (п.10).

Делает корректный многослойный PSD (8 бит, RGB, без сжатия каналов):
плоское сведённое изображение для превью + отдельные слои, которые
дизайнер может править независимо. Имена слоёв — ASCII (надёжно
открывается в любом Photoshop; русские имена не пишем, чтобы не словить
проблем с кодировкой legacy-Pascal-имени).

Сознательное ограничение: настоящие смарт-объекты (встроенные linked-
layer дескрипторы) здесь НЕ создаются — это отдельный риск-компонент.
Слои растровые; в Photoshop любой из них превращается в смарт-объект
в один клик (ПКМ → «Преобразовать в смарт-объект»). См. README.

Формат собран по спецификации Adobe Photoshop File Format (версия 1, PSD).
"""

from __future__ import annotations

import struct
from typing import Sequence

from PIL import Image


def _u8(v: int) -> bytes:
    return struct.pack(">B", v)


def _u16(v: int) -> bytes:
    return struct.pack(">H", v)


def _i16(v: int) -> bytes:
    return struct.pack(">h", v)


def _u32(v: int) -> bytes:
    return struct.pack(">I", v)


def _i32(v: int) -> bytes:
    return struct.pack(">i", v)


def _pascal_padded4(name: str) -> bytes:
    """Legacy Pascal-имя слоя: байт длины + ASCII-байты, паддинг всего поля до x4."""
    b = name.encode("ascii", "replace")[:255]
    s = _u8(len(b)) + b
    pad = (-len(s)) % 4
    return s + b"\x00" * pad


def _channels_rgba(img: Image.Image) -> tuple[bytes, bytes, bytes, bytes]:
    """Развести RGBA на планарные байтовые каналы (по строкам)."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    return r.tobytes(), g.tobytes(), b.tobytes(), a.tobytes()


# Порядок каналов в записи слоя: A(-1) нельзя ставить первым в record header,
# Photoshop ждёт R(0),G(1),B(2),A(-1).
_CHANNEL_IDS = (0, 1, 2, -1)


def write_psd(path: str, size: tuple[int, int],
              layers: Sequence[tuple[str, Image.Image]],
              composite: Image.Image) -> str:
    """Записать PSD по пути path.

    size      — (W, H) холста; composite должен быть этого размера.
    layers    — [(имя, RGBA-изображение на весь холст), ...] снизу вверх.
                Слои автоматически обрезаются до своего контента (п.5 v0.5).
    composite — сведённое RGBA для превью (например, итоговая плашка).
    """
    W, H = size
    out = bytearray()

    # ---- File header -----------------------------------------------------
    out += b"8BPS"
    out += _u16(1)                 # version = 1 (PSD)
    out += b"\x00" * 6             # reserved
    out += _u16(4)                 # channels merged (RGBA)
    out += _u32(H)
    out += _u32(W)
    out += _u16(8)                 # depth
    out += _u16(3)                 # color mode = RGB

    # ---- Color Mode Data -------------------------------------------------
    out += _u32(0)

    # ---- Image Resources -------------------------------------------------
    out += _u32(0)

    # ---- Layer and Mask Information --------------------------------------
    layer_records = bytearray()
    channel_data = bytearray()

    for name, img in layers:
        if img.size != (W, H):
            img = img.resize((W, H))

        # п.5 v0.5: обрезать слой до контента (tight bbox)
        bbox = img.getbbox()          # (left, top, right, bottom) или None
        if bbox is None:
            # пустой слой — делаем пустой 1×1 (порядок переменных как у PIL
            # bbox: left, top, right, bottom — раньше тут были перепутаны
            # местами top/left и bottom/right; сейчас безобидно, т.к. все
            # значения симметричны (0,0,1,1), но не дублируем рассинхрон)
            left, top, right, bottom = 0, 0, 1, 1
            cropped = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        else:
            left, top, right, bottom = bbox
            cropped = img.crop(bbox)

        chans = _channels_rgba(cropped)

        rec = bytearray()
        rec += _i32(top) + _i32(left) + _i32(bottom) + _i32(right)
        rec += _u16(4)                       # число каналов
        for cid, cbytes in zip(_CHANNEL_IDS, chans):
            rec += _i16(cid)
            rec += _u32(2 + len(cbytes))      # +2 байта поля compression
        rec += b"8BIM"
        rec += b"norm"
        rec += _u8(255)                      # opacity
        rec += _u8(0)                        # clipping
        rec += _u8(0)                        # flags
        rec += _u8(0)                        # filler

        extra = bytearray()
        extra += _u32(0)                     # layer mask data: нет
        extra += _u32(0)                     # blending ranges: нет
        extra += _pascal_padded4(name)       # имя слоя
        rec += _u32(len(extra))
        rec += extra
        layer_records += rec

        for cbytes in chans:
            channel_data += _u16(0)
            channel_data += cbytes

    layer_count = len(layers)
    layer_info = bytearray()
    layer_info += _i16(layer_count)
    layer_info += layer_records
    layer_info += channel_data
    if len(layer_info) % 2:                   # выравнивание до чётного
        layer_info += b"\x00"

    layer_and_mask = bytearray()
    layer_and_mask += _u32(len(layer_info))
    layer_and_mask += layer_info
    layer_and_mask += _u32(0)                 # global layer mask info: нет

    out += _u32(len(layer_and_mask))
    out += layer_and_mask

    # ---- Image Data (сведённое изображение, raw планарно) ----------------
    cr, cg, cb, ca = _channels_rgba(composite if composite.size == (W, H)
                                    else composite.resize((W, H)))
    out += _u16(0)                            # compression = raw
    out += cr + cg + cb + ca

    with open(path, "wb") as f:
        f.write(out)
    return path


__all__ = ["write_psd"]
