"""
Plashker — writer PSD с НАСТОЯЩИМИ смарт-объектами (п.4).

Каждый слой, у которого есть один исходный файл, записывается как смарт-объект
Photoshop: оригинальный файл встраивается в документ (linked layer, блок `lnk2`,
запись `liFD`) в ОРИГИНАЛЬНОМ разрешении, а слой помечается блоками `PlLd`
(Placed Layer) и `SoLd` (Smart Object Layer Data), которые ссылаются на встроенный
файл по uuid и задают трансформ размещения на холсте.

Байтовые структуры выверены по эталонному ридеру psd-tools (linked_layer.py,
tagged_blocks.py, descriptor.py):

  • lnk2 / liFD  — встраиваемый файл: 'liFD' + version + pascal(uuid) +
                  unicode(filename) + filetype + creator + u64(datasize) +
                  флаг open_file(0) + данные; записи в контейнере с 8-байтовым
                  префиксом длины и выравниванием до 4.
  • PlLd / plcL — version 3, uuid, page/total/anti_alias/type, 8 double трансформ,
                  warp как DescriptorBlock2 (2×u32 версии + дескриптор).
  • SoLd / soLD — version 4 + DescriptorBlock (u32 16 + дескриптор) с ключами
                  Idnt/placed/Sz/Rslt/Trnf/…

БЕЗОПАСНОСТЬ. В каждом слое СОХРАНЯЕТСЯ полноценный растровый канал (как в
psd.write_psd) — файл всегда открывается и выглядит правильно, даже если
Photoshop проигнорирует смарт-блоки. После записи вызывается _selfcheck(),
который заново разбирает файл и сверяет все длины секций и встроенные PNG; если
структура не сходится — writer поднимает исключение, и вызывающий код падает на
проверенный растровый psd.write_psd (см. project.export).

ВАЖНО: протестировать открытие в реальном Photoshop не было возможности в среде
сборки (нет ни Photoshop, ни валидатора psd-tools), поэтому смарт-режим включён
с автоматическим откатом.
"""

from __future__ import annotations

import io
import struct
import uuid as _uuid
from typing import Optional, Sequence

from PIL import Image


# ---------------------------------------------------------------------------
# Примитивы
# ---------------------------------------------------------------------------

def _u8(v: int) -> bytes:  return struct.pack(">B", v)
def _u16(v: int) -> bytes: return struct.pack(">H", v)
def _i16(v: int) -> bytes: return struct.pack(">h", v)
def _u32(v: int) -> bytes: return struct.pack(">I", v & 0xFFFFFFFF)
def _i32(v: int) -> bytes: return struct.pack(">i", v)
def _u64(v: int) -> bytes: return struct.pack(">Q", v)
def _f64(v: float) -> bytes: return struct.pack(">d", float(v))


def _pad4(n: int) -> int:
    return (-n) % 4


def _pascal(s: str) -> bytes:
    """Pascal-строка (1 байт длины + байты), macroman≈ascii, без доп.паддинга."""
    b = s.encode("macroman", "replace")[:255]
    return _u8(len(b)) + b


def _pascal_padded4(name: str) -> bytes:
    b = name.encode("ascii", "replace")[:255]
    s = _u8(len(b)) + b
    return s + b"\x00" * _pad4(len(s))


def _unicode(s: str) -> bytes:
    """Unicode-строка PSD: u32(число UTF-16 юнитов включая null) + UTF-16BE."""
    payload = (s + "\x00").encode("utf-16-be")
    return _u32(len(s) + 1) + payload


def _key(k: bytes) -> bytes:
    """Ключ дескриптора/classID: u32(0)+4 байта для 4-символьных, иначе u32(len)+байты."""
    if len(k) == 4:
        return _u32(0) + k
    return _u32(len(k)) + k


# ---------------------------------------------------------------------------
# Дескрипторы (OSType) — по descriptor.py psd-tools
# ---------------------------------------------------------------------------

def _desc_body(class_id: bytes, items: Sequence[bytes]) -> bytes:
    """Тело дескриптора: unicode(name='') + classID + u32(count) + items."""
    return _unicode("") + _key(class_id) + _u32(len(items)) + b"".join(items)


def _descriptor_block(class_id: bytes, items: Sequence[bytes]) -> bytes:
    """DescriptorBlock: u32(version=16) + тело (без внешнего паддинга здесь)."""
    return _u32(16) + _desc_body(class_id, items)


def _descriptor_block2(class_id: bytes, items: Sequence[bytes]) -> bytes:
    """DescriptorBlock2: u32(version=1) + u32(data_version=16) + тело."""
    return _u32(1) + _u32(16) + _desc_body(class_id, items)


# item-энкодеры (ключ + OSType + значение)
def it_text(key: bytes, s: str) -> bytes:  return _key(key) + b"TEXT" + _unicode(s)
def it_long(key: bytes, v: int) -> bytes:  return _key(key) + b"long" + _i32(v)
def it_doub(key: bytes, v: float) -> bytes: return _key(key) + b"doub" + _f64(v)
def it_bool(key: bytes, v: bool) -> bytes: return _key(key) + b"bool" + _u8(1 if v else 0)
def it_untf(key: bytes, unit: bytes, v: float) -> bytes:
    return _key(key) + b"UntF" + unit + _f64(v)
def it_enum(key: bytes, type_id: bytes, enum_id: bytes) -> bytes:
    return _key(key) + b"enum" + _key(type_id) + _key(enum_id)
def it_objc(key: bytes, class_id: bytes, items: Sequence[bytes]) -> bytes:
    return _key(key) + b"Objc" + _desc_body(class_id, items)
def it_vlls_doub(key: bytes, vals: Sequence[float]) -> bytes:
    return _key(key) + b"VlLs" + _u32(len(vals)) + b"".join(b"doub" + _f64(v) for v in vals)


# ---------------------------------------------------------------------------
# Смарт-объектные блоки
# ---------------------------------------------------------------------------

def _tagged(key: bytes, data: bytes) -> bytes:
    """Тэг-блок доп.информации слоя/документа: '8BIM'+key+u32(len)+data (pad→4).

    Длину объявляем УЖЕ выровненной на 4, чтобы ни один ридер не съезжал на
    паддинге (skip=0 при кратности 4)."""
    data = data + b"\x00" * _pad4(len(data))
    return b"8BIM" + key + _u32(len(data)) + data


def _linked_record(uid: str, filename: str, data: bytes) -> bytes:
    """Одна запись встраиваемого файла (liFD, version 2)."""
    r = bytearray()
    r += b"liFD"
    r += _u32(2)
    r += _pascal(uid)
    r += _unicode(filename)
    r += b"PNG "                 # filetype (встраиваем PNG-оригиналы)
    r += b"\x00\x00\x00\x00"     # creator
    r += _u64(len(data))
    r += _u8(0)                  # open_file descriptor отсутствует
    r += data
    return bytes(r)


def _lnk2_block(entries: Sequence[tuple]) -> bytes:
    """Документный блок lnk2 со всеми встроенными файлами.

    entries: [(uid, filename, data), ...]. Контейнер: на запись — u64 длины +
    запись + выравнивание до 4."""
    body = bytearray()
    for uid, filename, data in entries:
        rec = _linked_record(uid, filename, data)
        body += _u64(len(rec))
        body += rec
        body += b"\x00" * _pad4(len(rec))
    return _tagged(b"lnk2", bytes(body))


def _obar_mesh_item(ow: int, oh: int) -> bytes:
    """meshPoints как ObAr (object array) — регулярная сетка 4×4 по размерам
    оригинала (identity-warp). Ровно то, что пишет Photoshop для warpCustom.

    ObAr: u32(length) + unicode(name) + key(classID) + u32(fieldCount) + поля.
    Поле: key + 'UnFl' + unit(4) + u32(count) + count×f64."""
    cols = [0.0, ow / 3.0, 2.0 * ow / 3.0, float(ow)]
    rows = [0.0, oh / 3.0, 2.0 * oh / 3.0, float(oh)]
    hrzn, vrtc = [], []
    for r in range(4):
        for c in range(4):
            hrzn.append(cols[c])
            vrtc.append(rows[r])

    def field(key: bytes, vals) -> bytes:
        return (_key(key) + b"UnFl" + b"#Pxl" + _u32(len(vals))
                + b"".join(_f64(v) for v in vals))

    body = (_u32(16) + _unicode("") + _key(b"rationalPoint") + _u32(2)
            + field(b"Hrzn", hrzn) + field(b"Vrtc", vrtc))
    return _key(b"meshPoints") + b"ObAr" + body


def _warp_items(ow: int, oh: int) -> list:
    """9 ключей warp-дескриптора Photoshop (warpCustom + меш). bounds — в
    размерах ОРИГИНАЛА (не холста)."""
    return [
        it_enum(b"warpStyle", b"warpStyle", b"warpCustom"),
        it_doub(b"warpValue", 0.0),
        it_doub(b"warpPerspective", 0.0),
        it_doub(b"warpPerspectiveOther", 0.0),
        it_enum(b"warpRotate", b"Ornt", b"Hrzn"),
        it_objc(b"bounds", b"classFloatRect", [
            it_doub(b"Top ", 0.0),
            it_doub(b"Left", 0.0),
            it_doub(b"Btom", float(oh)),
            it_doub(b"Rght", float(ow)),
        ]),
        it_long(b"uOrder", 4),
        it_long(b"vOrder", 4),
        it_objc(b"customEnvelopeWarp", b"customEnvelopeWarp", [_obar_mesh_item(ow, oh)]),
    ]


def _plld_block(uid: str, transform, ow: int, oh: int) -> bytes:
    """Блок PlLd (Placed Layer, version 3) — точно как пишет Photoshop."""
    b = bytearray()
    b += b"plcL"
    b += _u32(3)
    b += _pascal(uid)            # GUID с дефисами, без паддинга
    b += _u32(1)                 # page
    b += _u32(1)                 # total pages
    b += _u32(16)                # anti-alias
    b += _u32(2)                 # layer type
    for v in transform:
        b += _f64(v)
    # warp: u32(0) [версия warp] + u32(16) [версия дескриптора] + тело
    b += _u32(0)
    b += _descriptor_block(b"warp", _warp_items(ow, oh))
    return _tagged(b"PlLd", bytes(b))


def _sold_block(uid_idnt: str, uid_placed: str, transform, ow: int, oh: int) -> bytes:
    """Блок SoLd (Smart Object Layer Data, version 4) — 18 ключей как у Photoshop."""
    items = [
        it_text(b"Idnt", uid_idnt),
        it_text(b"placed", uid_placed),
        it_long(b"PgNm", 1),
        it_long(b"totalPages", 1),
        it_long(b"Crop", 1),
        it_objc(b"frameStep", b"null", [
            it_long(b"numerator", 0), it_long(b"denominator", 600)]),
        it_objc(b"duration", b"null", [
            it_long(b"numerator", 0), it_long(b"denominator", 600)]),
        it_long(b"frameCount", 1),
        it_long(b"Annt", 16),
        it_long(b"Type", 2),
        it_vlls_doub(b"Trnf", transform),
        it_vlls_doub(b"nonAffineTransform", transform),
        it_objc(b"warp", b"warp", _warp_items(ow, oh)),
        it_objc(b"Sz  ", b"Pnt ", [
            it_doub(b"Wdth", float(ow)), it_doub(b"Hght", float(oh))]),
        it_untf(b"Rslt", b"#Rsl", 72.0),
        it_long(b"comp", -1),
        it_objc(b"compInfo", b"null", [
            it_long(b"compID", -1), it_long(b"originalCompID", -1)]),
        it_objc(b"ClMg", b"ClMg", [
            it_enum(b"placedLayerOCIOConversion",
                    b"placedLayerOCIOConversion",
                    b"placedLayerOCIOConvertEmbedded")]),
    ]
    body = b"soLD" + _u32(4) + _descriptor_block(b"null", items)
    return _tagged(b"SoLd", bytes(body))


# ---------------------------------------------------------------------------
# Каналы / растровая часть слоя (как в psd.write_psd)
# ---------------------------------------------------------------------------

_CHANNEL_IDS = (0, 1, 2, -1)


def _channels_rgba(img: Image.Image):
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    return r.tobytes(), g.tobytes(), b.tobytes(), a.tobytes()


# ---------------------------------------------------------------------------
# Основной writer
# ---------------------------------------------------------------------------

def write_psd_smart(path: str, size: tuple[int, int],
                    layers: Sequence[dict], composite: Image.Image) -> str:
    """Записать PSD со смарт-объектами.

    layers: [{name, image (RGBA на весь холст), smart: dict|None}, ...] снизу
    вверх. Если smart is not None — слой будет смарт-объектом со встроенным
    оригиналом (smart['data'], smart['orig_size'], smart['transform']).
    """
    W, H = size
    out = bytearray()

    # ---- File header ----
    out += b"8BPS"
    out += _u16(1)
    out += b"\x00" * 6
    out += _u16(4)
    out += _u32(H)
    out += _u32(W)
    out += _u16(8)
    out += _u16(3)

    out += _u32(0)                # Color Mode Data
    out += _u32(0)                # Image Resources

    # ---- Layer records + channel data ----
    layer_records = bytearray()
    channel_data = bytearray()
    linked_entries: list[tuple] = []

    for spec in layers:
        name = spec["name"]
        img = spec["image"]
        smart = spec.get("smart")
        if img.size != (W, H):
            img = img.resize((W, H))

        bbox = img.getbbox()
        if bbox is None:
            left, top, right, bottom = 0, 0, 1, 1
            cropped = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        else:
            left, top, right, bottom = bbox
            cropped = img.crop(bbox)

        chans = _channels_rgba(cropped)

        rec = bytearray()
        rec += _i32(top) + _i32(left) + _i32(bottom) + _i32(right)
        rec += _u16(4)
        for cid, cbytes in zip(_CHANNEL_IDS, chans):
            rec += _i16(cid)
            rec += _u32(2 + len(cbytes))
        rec += b"8BIM"
        rec += b"norm"
        rec += _u8(255)
        rec += _u8(0)
        rec += _u8(0)
        rec += _u8(0)

        extra = bytearray()
        extra += _u32(0)          # layer mask data
        extra += _u32(0)          # blending ranges
        extra += _pascal_padded4(name)

        # смарт-объектные блоки (если есть один исходник и слой непустой)
        if smart and bbox is not None:
            # GUID с дефисами, как у Photoshop; Idnt (=uid в lnk2 и pascal PlLd)
            # и placed — РАЗНЫЕ идентификаторы (так пишет Photoshop).
            uid = str(_uuid.uuid4())
            placed = str(_uuid.uuid4())
            ow, oh = smart["orig_size"]
            transform = smart["transform"]
            fname = f"{name}.png".replace(" ", "_")
            linked_entries.append((uid, fname, smart["data"]))
            extra += _plld_block(uid, transform, ow, oh)
            extra += _sold_block(uid, placed, transform, ow, oh)

        rec += _u32(len(extra))
        rec += extra
        layer_records += rec

        for cbytes in chans:
            channel_data += _u16(0)     # compression = raw
            channel_data += cbytes

    layer_count = len(layers)
    layer_info = bytearray()
    layer_info += _i16(layer_count)
    layer_info += layer_records
    layer_info += channel_data
    if len(layer_info) % 2:
        layer_info += b"\x00"

    # ---- документные доп.блоки: lnk2 со встроенными файлами ----
    doc_additional = bytearray()
    if linked_entries:
        doc_additional += _lnk2_block(linked_entries)

    layer_and_mask = bytearray()
    layer_and_mask += _u32(len(layer_info))
    layer_and_mask += layer_info
    layer_and_mask += _u32(0)          # global layer mask info
    layer_and_mask += doc_additional

    out += _u32(len(layer_and_mask))
    out += layer_and_mask

    # ---- Image Data (сведённое) ----
    cr, cg, cb, ca = _channels_rgba(composite if composite.size == (W, H)
                                    else composite.resize((W, H)))
    out += _u16(0)
    out += cr + cg + cb + ca

    with open(path, "wb") as f:
        f.write(bytes(out))

    _selfcheck(bytes(out), (W, H), linked_entries)
    return path


# ---------------------------------------------------------------------------
# Самопроверка структуры: заново разбираем файл и сверяем длины/встроенные PNG.
# Ловит рассинхрон рамок ДО того, как файл попадёт в Photoshop.
# ---------------------------------------------------------------------------

def _selfcheck(buf: bytes, size: tuple[int, int], linked_entries: Sequence[tuple]) -> None:
    W, H = size
    f = io.BytesIO(buf)

    def rd(fmt):
        n = struct.calcsize(fmt)
        return struct.unpack(fmt, f.read(n))

    if f.read(4) != b"8BPS":
        raise ValueError("psd_smart: нет сигнатуры 8BPS")
    (ver,) = rd(">H")
    f.read(6)
    (chans,) = rd(">H")
    (h,) = rd(">I")
    (w,) = rd(">I")
    (depth,) = rd(">H")
    (mode,) = rd(">H")
    if (w, h) != (W, H):
        raise ValueError("psd_smart: размер холста не совпал")
    (cm_len,) = rd(">I"); f.read(cm_len)
    (ir_len,) = rd(">I"); f.read(ir_len)

    (lam_len,) = rd(">I")
    lam_start = f.tell()
    (li_len,) = rd(">I")
    li_start = f.tell()
    (count,) = rd(">h")
    count = abs(count)
    # пробегаем записи слоёв
    total_channels = 0
    chan_lens = []
    for _i in range(count):
        top, left, bottom, right = rd(">iiii")
        (nch,) = rd(">H")
        this = []
        for _c in range(nch):
            (cid,) = rd(">h")
            (clen,) = rd(">I")
            this.append(clen)
        sig = f.read(4)
        if sig != b"8BIM":
            raise ValueError("psd_smart: битый blend-signature слоя")
        f.read(4)                 # blend mode
        f.read(4)                 # opacity/clip/flags/filler
        (extra_len,) = rd(">I")
        extra_end = f.tell() + extra_len
        if extra_end > li_start + li_len:
            raise ValueError("psd_smart: extra-блок слоя вышел за границы layer_info")
        f.seek(extra_end)
        chan_lens.append(this)
    # каналы
    for this in chan_lens:
        for clen in this:
            f.read(clen)
    # выравнивание layer_info
    consumed = f.tell() - li_start
    if consumed > li_len:
        raise ValueError("psd_smart: layer_info переполнен")
    f.seek(li_start + li_len)
    # global layer mask
    (glm_len,) = rd(">I"); f.read(glm_len)
    # документные доп.блоки (должен быть lnk2, если есть встраивания)
    add_end = lam_start + lam_len
    found_lnk = 0
    embedded_ok = 0
    while f.tell() + 12 <= add_end:
        sig = f.read(4)
        if sig not in (b"8BIM", b"8B64"):
            break
        key = f.read(4)
        (blen,) = rd(">I")
        block_start = f.tell()
        if key == b"lnk2":
            found_lnk += 1
            sub = io.BytesIO(f.read(blen))
            while True:
                head = sub.read(8)
                if len(head) < 8:
                    break
                (rlen,) = struct.unpack(">Q", head)
                if rlen == 0 or sub.tell() + rlen > blen:
                    break
                rec = sub.read(rlen)
                sub.read(_pad4(rlen))
                # проверяем, что встроенные данные — валидная картинка
                try:
                    kind = rec[:4]
                    if kind == b"liFD":
                        # найти начало данных: пропускаем version(4)+pascal+unicode+8+8(u64)+1
                        p = 4 + 4
                        plen = rec[p]; p += 1 + plen
                        (uni,) = struct.unpack(">I", rec[p:p+4]); p += 4 + uni * 2
                        p += 4 + 4                      # filetype+creator
                        (dsize,) = struct.unpack(">Q", rec[p:p+8]); p += 8
                        p += 1                          # open_file flag
                        blob = rec[p:p+dsize]
                        Image.open(io.BytesIO(blob)).load()
                        embedded_ok += 1
                except Exception as e:
                    raise ValueError(f"psd_smart: встроенный файл не парсится: {e}")
        f.seek(block_start + blen)

    if linked_entries:
        if not found_lnk:
            raise ValueError("psd_smart: блок lnk2 не найден при наличии смарт-объектов")
        if embedded_ok < len(linked_entries):
            raise ValueError("psd_smart: не все встроенные файлы прочитались обратно")

    # image data
    (comp,) = rd(">H")
    expected = W * H * 4
    remaining = len(buf) - f.tell()
    if remaining < expected:
        raise ValueError("psd_smart: image data короче ожидаемого")


__all__ = ["write_psd_smart"]
