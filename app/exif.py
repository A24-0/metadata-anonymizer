from __future__ import annotations
import struct
from fractions import Fraction
from typing import Any, Iterable, Mapping
__all__ = ['ExifError', 'Exif', 'IFD0', 'IFD_EXIF', 'IFD_GPS', 'IFD_INTEROP', 'parse_tiff', 'parse_exif_block', 'ascii_value', 'rational_to_float', 'dms_to_decimal', 'decimal_to_dms', 'format_dms', 'format_gps', 'parse_xmp_coord']
IFD0 = 'ifd0'
IFD_EXIF = 'exif'
IFD_GPS = 'gps'
IFD_INTEROP = 'interop'
TAG_EXIF_IFD = 34665
TAG_GPS_IFD = 34853
TAG_INTEROP_IFD = 40965
TYPE_SIZES: dict[int, int] = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_TYPE_FMT: dict[int, str] = {1: 'B', 3: 'H', 4: 'I', 6: 'b', 8: 'h', 9: 'i', 11: 'f', 12: 'd'}
MAX_IFD_DEPTH = 4

class ExifError(ValueError):
    pass

class Exif:

    def __init__(self, values: Mapping[str, Mapping[int, Any]], byte_order: str, warnings: Iterable[str]=()) -> None:
        self.values: dict[str, dict[int, Any]] = {name: dict(ifd) for name, ifd in values.items()}
        self.byte_order = byte_order
        self.warnings: list[str] = list(warnings)

    def ifd(self, name: str=IFD0) -> dict[int, Any]:
        return self.values.get(name, {})

    def get(self, tag: int, ifd: str=IFD0) -> Any:
        return self.ifd(ifd).get(tag)

    def text(self, tag: int, ifd: str=IFD0) -> str | None:
        value = self.get(tag, ifd)
        if isinstance(value, bytes):
            value = ascii_value(value)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def __bool__(self) -> bool:
        return any(self.values.values())

    def __repr__(self) -> str:
        sizes = ', '.join((f'{k}={len(v)}' for k, v in self.values.items()))
        return f'<Exif {self.byte_order} {sizes}>'

def parse_exif_block(data: bytes, warnings: list[str] | None=None) -> Exif:
    if data.startswith(b'Exif\x00\x00'):
        data = data[6:]
    return parse_tiff(data, warnings)

def parse_tiff(data: bytes, warnings: list[str] | None=None) -> Exif:
    notes: list[str] = warnings if warnings is not None else []
    if len(data) < 8:
        raise ExifError(f'TIFF header too short: {len(data)} bytes')
    order = data[:2]
    if order == b'II':
        endian = '<'
    elif order == b'MM':
        endian = '>'
    else:
        raise ExifError(f'unknown TIFF byte order {order!r}')
    magic = struct.unpack(endian + 'H', data[2:4])[0]
    if magic != 42:
        raise ExifError(f'bad TIFF magic {magic} (expected 42)')
    first_ifd = struct.unpack(endian + 'I', data[4:8])[0]
    if first_ifd < 8 or first_ifd >= len(data):
        raise ExifError(f'IFD0 offset {first_ifd} outside TIFF block ({len(data)} bytes)')
    values: dict[str, dict[int, Any]] = {IFD0: {}, IFD_EXIF: {}, IFD_GPS: {}, IFD_INTEROP: {}}
    visited: set[int] = set()
    ifd0 = _read_ifd(data, endian, first_ifd, IFD0, notes, visited, 0)
    values[IFD0] = ifd0
    plan = ((TAG_EXIF_IFD, IFD_EXIF), (TAG_GPS_IFD, IFD_GPS), (TAG_INTEROP_IFD, IFD_INTEROP))
    for pointer_tag, name in plan:
        offset = ifd0.get(pointer_tag)
        if not isinstance(offset, int) or offset <= 0:
            continue
        try:
            values[name] = _read_ifd(data, endian, offset, name, notes, visited, 1)
        except ExifError as exc:
            notes.append(f'{name}_ifd_unreadable: {exc}')
    return Exif(values, order.decode('ascii'), notes)

def _read_ifd(data: bytes, endian: str, offset: int, name: str, warnings: list[str], visited: set[int], depth: int) -> dict[int, Any]:
    if depth > MAX_IFD_DEPTH:
        raise ExifError(f'IFD nesting deeper than {MAX_IFD_DEPTH}')
    if offset in visited:
        raise ExifError(f'IFD loop at offset {offset}')
    if offset + 2 > len(data):
        raise ExifError(f'{name} offset {offset} outside TIFF block')
    visited.add(offset)
    count = struct.unpack(endian + 'H', data[offset:offset + 2])[0]
    entries_start = offset + 2
    entries_end = entries_start + count * 12
    if entries_end > len(data):
        raise ExifError(f'{name} declares {count} entries ({entries_end - offset} bytes) but block is {len(data)} bytes')
    out: dict[int, Any] = {}
    for idx in range(count):
        pos = entries_start + idx * 12
        entry = data[pos:pos + 12]
        tag, fmt = struct.unpack(endian + 'HH', entry[0:4])
        num = struct.unpack(endian + 'I', entry[4:8])[0]
        try:
            value = _decode_value(data, endian, fmt, num, entry[8:12])
        except ExifError as exc:
            warnings.append(f'{name}_entry_skipped: tag 0x{tag:04X}: {exc}')
            continue
        if value is not None:
            out[tag] = value
    return out

def _decode_value(data: bytes, endian: str, fmt: int, num: int, field: bytes) -> Any:
    if fmt not in TYPE_SIZES:
        raise ExifError(f'unknown field type {fmt}')
    if num == 0:
        raise ExifError('zero count')
    unit = TYPE_SIZES[fmt]
    total = unit * num
    if total <= 4:
        raw = field[:total]
    else:
        offset = struct.unpack(endian + 'I', field)[0]
        if offset + total > len(data):
            raise ExifError(f'value at offset {offset} (+{total}) outside TIFF block ({len(data)} bytes)')
        raw = data[offset:offset + total]
    if fmt == 2:
        return ascii_value(raw)
    if fmt == 7:
        return raw
    if fmt in (5, 10):
        signed = fmt == 10
        code = 'i' if signed else 'I'
        parts: list[Fraction] = []
        for i in range(num):
            numer, denom = struct.unpack(endian + code * 2, raw[i * 8:(i + 1) * 8])
            parts.append(Fraction(numer, denom) if denom else Fraction(0))
        return parts[0] if num == 1 else tuple(parts)
    if fmt == 1 and num > 4:
        return raw
    code = _TYPE_FMT[fmt]
    values = struct.unpack(endian + code * num, raw)
    return values[0] if num == 1 else tuple(values)

def ascii_value(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    cut = raw.split(b'\x00', 1)[0]
    for enc in ('utf-8', 'cp1251', 'latin-1'):
        try:
            return cut.decode(enc)
        except UnicodeDecodeError:
            continue
    return cut.decode('latin-1', 'replace')

def rational_to_float(value: Any) -> float | None:
    if isinstance(value, tuple):
        if not value:
            return None
        value = value[0]
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None

def dms_to_decimal(parts: Iterable[Any]) -> float | None:
    vals = [rational_to_float(p) for p in parts]
    if not vals or any((v is None for v in vals)):
        return None
    deg = vals[0] or 0.0
    minutes = vals[1] if len(vals) > 1 else 0.0
    seconds = vals[2] if len(vals) > 2 else 0.0
    return deg + (minutes or 0.0) / 60.0 + (seconds or 0.0) / 3600.0

def decimal_to_dms(value: float) -> tuple[int, int, float]:
    negative = value < 0
    value = abs(value)
    deg = int(value)
    minutes_full = (value - deg) * 60.0
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60.0, 2)
    if seconds >= 60.0:
        seconds -= 60.0
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        deg += 1
    return (-deg if negative else deg, minutes, seconds)

def _sec_text(seconds: float) -> str:
    if abs(seconds - round(seconds)) < 1e-09:
        return str(int(round(seconds)))
    return f'{seconds:.2f}'.rstrip('0').rstrip('.')

def format_dms(parts: Iterable[Any], ref: str='') -> str:
    decimal = dms_to_decimal(parts)
    if decimal is None:
        return ''
    deg, minutes, seconds = decimal_to_dms(abs(decimal))
    sign = '-' if decimal < 0 else ''
    return f'''{sign}{deg}°{minutes}'{_sec_text(seconds)}"{ref or ''}'''

def format_gps(lat: Any, lat_ref: str | None, lon: Any, lon_ref: str | None, altitude: Any=None, altitude_ref: int | None=None) -> str | None:
    if not lat or not lon:
        return None
    lat_text = format_dms(lat, _ref_text(lat_ref, 'N', 'S'))
    lon_text = format_dms(lon, _ref_text(lon_ref, 'E', 'W'))
    if not lat_text or not lon_text:
        return None
    text = f'{lat_text}, {lon_text}'
    alt = rational_to_float(altitude)
    if alt is not None:
        sign = '-' if altitude_ref == 1 else ''
        text += f', {sign}{alt:.1f} m'
    return text

def _ref_text(ref: Any, positive: str, negative: str) -> str:
    if isinstance(ref, bytes):
        ref = ref.decode('latin-1', 'replace')
    text = (str(ref).strip() if ref is not None else '') or positive
    return text[:1].upper()

def _coord_numbers(raw: str) -> list[float] | None:
    cleaned = raw
    for ch in '°\'"dDmM':
        cleaned = cleaned.replace(ch, ' ')
    numbers: list[float] = []
    for segment in cleaned.split(','):
        for token in segment.split():
            try:
                numbers.append(float(token))
            except ValueError:
                return None
    return numbers

def parse_xmp_coord(text: str) -> tuple[float, str] | None:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    ref = ''
    if raw[-1].upper() in {'N', 'S', 'E', 'W'}:
        ref = raw[-1].upper()
        raw = raw[:-1]
    numbers = _coord_numbers(raw)
    if not numbers:
        return None
    if len(numbers) == 1:
        if not ref:
            return None
        decimal = numbers[0]
    elif len(numbers) == 2:
        decimal = numbers[0] + numbers[1] / 60.0
    else:
        decimal = numbers[0] + numbers[1] / 60.0 + numbers[2] / 3600.0
    if ref in {'S', 'W'}:
        decimal = -decimal
    deg, minutes, seconds = decimal_to_dms(abs(decimal))
    sign = '-' if decimal < 0 else ''
    readable = f'''{sign}{deg}°{minutes}'{_sec_text(seconds)}"{ref}'''
    return (decimal, readable)
