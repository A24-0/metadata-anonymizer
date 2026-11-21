from __future__ import annotations
import os
import tempfile
from . import contract
from . import exif as exif_mod
from . import xmp as xmp_mod
from .base import BaseHandler
from .fields import FieldCollector
from .registry import register
__all__ = ['JpegHandler', 'JpegStructureError', 'scan_jpeg', 'strip_metadata_segments', 'EXIF_SIG', 'XMP_SIG']
EXIF_SIG = b'Exif\x00\x00'
XMP_SIG = b'http://ns.adobe.com/xap/1.0/\x00'
TAG_DESC = 270
TAG_MAKE = 271
TAG_MODEL = 272
TAG_SOFTWARE = 305
TAG_DATETIME = 306
TAG_ARTIST = 315
TAG_COPYRIGHT = 33432
TAG_DT_ORIGINAL = 36867
TAG_DT_DIGITIZED = 36868
TAG_BODY_SERIAL = 42033
GPS_LAT_REF = 1
GPS_LAT = 2
GPS_LON_REF = 3
GPS_LON = 4
GPS_ALT_REF = 5
GPS_ALT = 6
_STANDALONE = {1, 216, 217, *range(208, 216)}
_SOF = {*range(192, 208)} - {196, 200, 204}
_TRAILING = b'\x00\r\n\t '
_STRIPPED_APP = set(range(225, 240)) - {226, 238}
WARN_CLEAN_PARTIAL = 'clean_partial'
WARN_CLEAN_NO_OUT = 'clean_no_out_path'

class JpegStructureError(ValueError):
    pass

def scan_jpeg(data: bytes) -> tuple[bytes | None, bytes | None]:
    if not data.startswith(b'\xff\xd8'):
        raise JpegStructureError(f"no SOI marker: first bytes are {data[:4].hex() or '<empty>'}, not a JPEG")
    exif_block: bytes | None = None
    xmp_block: bytes | None = None
    found_sof = found_sos = False
    n = len(data)
    i = 2
    while True:
        if i >= n:
            raise JpegStructureError(f'unexpected end of file at offset {i}')
        if data[i] != 255:
            raise JpegStructureError(f'stray byte 0x{data[i]:02x} at offset {i}: marker expected')
        k = i
        while k < n and data[k] == 255:
            k += 1
        if k >= n:
            raise JpegStructureError('file ends inside marker')
        marker = data[k]
        if marker == 217:
            break
        if marker in _STANDALONE:
            i = k + 1
            continue
        if k + 3 > n:
            raise JpegStructureError(f'truncated segment header at offset {k}')
        length = int.from_bytes(data[k + 1:k + 3], 'big')
        if length < 2:
            raise JpegStructureError(f'invalid segment length {length} at offset {k}')
        seg_end = k + 1 + length
        if seg_end > n:
            raise JpegStructureError(f'truncated segment 0xFF{marker:02X}: needs {seg_end - k} bytes, only {n - k} left')
        body = data[k + 3:seg_end]
        if marker == 225 and exif_block is None and body.startswith(EXIF_SIG):
            exif_block = body[len(EXIF_SIG):]
        elif marker == 225 and xmp_block is None and body.startswith(XMP_SIG):
            xmp_block = body[len(XMP_SIG):]
        if marker in _SOF:
            found_sof = True
        if marker == 218:
            found_sos = True
            break
        i = seg_end
    if not found_sof:
        raise JpegStructureError('no SOF marker: image parameters missing')
    if not found_sos:
        raise JpegStructureError('no SOS marker: image data missing')
    tail = data.rstrip(_TRAILING)
    if not tail.endswith(b'\xff\xd9'):
        raise JpegStructureError('image data truncated: EOI marker (FFD9) not found at end of file')
    return (exif_block, xmp_block)

def _segment_label(marker: int, body: bytes) -> str:
    if marker == 254:
        return 'COM'
    if marker == 225 and body.startswith(EXIF_SIG):
        return 'APP1/EXIF'
    if marker == 225 and body.startswith(XMP_SIG):
        return 'APP1/XMP'
    if marker == 225:
        return 'APP1'
    if marker == 237:
        return 'APP13/IPTC'
    return f'APP{marker - 224}'

def _frame_size(data: bytes) -> tuple[int, int] | None:
    n = len(data)
    i = 2
    while i + 3 < n:
        if data[i] != 255:
            return None
        k = i
        while k < n and data[k] == 255:
            k += 1
        if k >= n:
            return None
        marker = data[k]
        if marker in _STANDALONE:
            i = k + 1
            continue
        length = int.from_bytes(data[k + 1:k + 3], 'big')
        if length < 2:
            return None
        if marker in _SOF and k + 1 + length <= n:
            body = data[k + 3:k + 1 + length]
            if len(body) >= 5:
                return (int.from_bytes(body[3:5], 'big'), int.from_bytes(body[1:3], 'big'))
            return None
        if marker == 218:
            return None
        i = k + 1 + length
    return None

def strip_metadata_segments(data: bytes) -> tuple[bytes, list[str]]:
    scan_jpeg(data)
    out = bytearray(data[:2])
    removed: list[str] = []
    n = len(data)
    i = 2
    while True:
        if i >= n:
            raise JpegStructureError(f'unexpected end of file at offset {i}')
        if data[i] != 255:
            raise JpegStructureError(f'stray byte 0x{data[i]:02x} at offset {i}: marker expected')
        k = i
        while k < n and data[k] == 255:
            k += 1
        if k >= n:
            raise JpegStructureError('file ends inside marker')
        marker = data[k]
        if marker == 217:
            out += data[i:]
            break
        if marker in _STANDALONE:
            out += data[i:k + 1]
            i = k + 1
            continue
        if k + 3 > n:
            raise JpegStructureError(f'truncated segment header at offset {k}')
        length = int.from_bytes(data[k + 1:k + 3], 'big')
        if length < 2:
            raise JpegStructureError(f'invalid segment length {length} at offset {k}')
        seg_end = k + 1 + length
        if seg_end > n:
            raise JpegStructureError(f'truncated segment 0xFF{marker:02X}: needs {seg_end - k} bytes, only {n - k} left')
        body = data[k + 3:seg_end]
        if marker in _STRIPPED_APP or marker == 254:
            removed.append(_segment_label(marker, body))
        elif marker == 218:
            out += data[i:]
            break
        else:
            out += data[i:seg_end]
        i = seg_end
    return (bytes(out), removed)

def _atomic_write(out_path: str, payload: bytes, mode_from: str | None=None) -> None:
    directory = os.path.dirname(os.path.abspath(out_path)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.clean-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if mode_from is not None:
            try:
                os.chmod(tmp, os.stat(mode_from).st_mode & 511)
            except OSError:
                pass
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def _exif_gps(ex: exif_mod.Exif) -> tuple[str | None, str | None]:
    lat = ex.get(GPS_LAT, exif_mod.IFD_GPS)
    lon = ex.get(GPS_LON, exif_mod.IFD_GPS)
    if not lat or not lon:
        return (None, None)
    text = exif_mod.format_gps(lat, ex.text(GPS_LAT_REF, exif_mod.IFD_GPS), lon, ex.text(GPS_LON_REF, exif_mod.IFD_GPS), ex.get(GPS_ALT, exif_mod.IFD_GPS), ex.get(GPS_ALT_REF, exif_mod.IFD_GPS))
    lat_dec = exif_mod.dms_to_decimal(lat)
    lon_dec = exif_mod.dms_to_decimal(lon)
    token = f'{lat_dec:.7f},{lon_dec:.7f}' if lat_dec is not None and lon_dec is not None else None
    return (text, token)

def _xmp_gps(xp: xmp_mod.XmpPackage) -> tuple[str | None, str | None]:
    lat_raw = xp.text('exif', 'GPSLatitude')
    lon_raw = xp.text('exif', 'GPSLongitude')
    if not lat_raw or not lon_raw:
        return (None, None)
    lat = exif_mod.parse_xmp_coord(lat_raw)
    lon = exif_mod.parse_xmp_coord(lon_raw)
    if not lat or not lon:
        return (None, None)
    text = f'{lat[1]}, {lon[1]}'
    alt = xp.text('exif', 'GPSAltitude')
    if alt:
        text += f', {alt}'
    return (text, f'{lat[0]:.7f},{lon[0]:.7f}')

def _exif_text(c: FieldCollector, key: str, ex: exif_mod.Exif, tag: int, ifd: str=exif_mod.IFD0, source: str='EXIF') -> None:
    text = ex.text(tag, ifd)
    if text is None and ex.get(tag, ifd) is not None:
        c.add(key, '', source)
        return
    c.add(key, text, source)

def _xmp_text(c: FieldCollector, key: str, xp: xmp_mod.XmpPackage, prefix: str, name: str, source: str='XMP') -> None:
    text = xp.text(prefix, name)
    if text is None and xp.has(prefix, name):
        c.add(key, '', source)
        return
    c.add(key, text, source)

def collect_fields(ex: exif_mod.Exif | None, xp: xmp_mod.XmpPackage | None, fmt: str=contract.FORMAT_JPEG, collector: FieldCollector | None=None, exif_source: str='EXIF') -> FieldCollector:
    c = collector if collector is not None else FieldCollector(fmt)
    if ex:
        _exif_text(c, 'author', ex, TAG_ARTIST, source=exif_source)
        _exif_text(c, 'software', ex, TAG_SOFTWARE, source=exif_source)
        gps_text, gps_token = _exif_gps(ex)
        c.add('gps', gps_text, exif_source, token=gps_token)
        taken = ex.text(TAG_DT_ORIGINAL, exif_mod.IFD_EXIF) or ex.text(TAG_DT_DIGITIZED, exif_mod.IFD_EXIF)
        c.add_date('date_taken', taken, exif_source)
        c.add_date('date_modified', ex.text(TAG_DATETIME), exif_source)
        _exif_text(c, 'device_make', ex, TAG_MAKE, source=exif_source)
        _exif_text(c, 'device_model', ex, TAG_MODEL, source=exif_source)
        _exif_text(c, 'serial', ex, TAG_BODY_SERIAL, exif_mod.IFD_EXIF, source=exif_source)
        _exif_text(c, 'copyright', ex, TAG_COPYRIGHT, source=exif_source)
        _exif_text(c, 'description', ex, TAG_DESC, source=exif_source)
        for warning in ex.warnings:
            c.note(warning)
    if xp:
        _xmp_text(c, 'author', xp, 'dc', 'creator')
        _xmp_text(c, 'software', xp, 'xmp', 'CreatorTool')
        gps_text, gps_token = _xmp_gps(xp)
        c.add('gps', gps_text, 'XMP', token=gps_token)
        c.add_date('date_taken', xp.text('xmp', 'CreateDate') or xp.text('dcterms', 'created') or xp.text('exif', 'DateTimeOriginal'), 'XMP')
        c.add_date('date_modified', xp.text('xmp', 'ModifyDate') or xp.text('dcterms', 'modified'), 'XMP')
        _xmp_text(c, 'device_make', xp, 'tiff', 'Make')
        _xmp_text(c, 'device_model', xp, 'tiff', 'Model')
        _xmp_text(c, 'serial', xp, 'exif', 'BodySerialNumber')
        _xmp_text(c, 'copyright', xp, 'dc', 'rights')
        _xmp_text(c, 'description', xp, 'dc', 'description')
        c.add('xmp_history', xp.history(), 'XMP')
    return c

@register
class JpegHandler(BaseHandler):
    format = contract.FORMAT_JPEG
    extensions = ('.jpg', '.jpeg', '.jpe', '.jfif')

    def _collector(self, path: str) -> FieldCollector:
        with open(path, 'rb') as fh:
            data = fh.read()
        exif_block, xmp_block = scan_jpeg(data)
        notes: list[str] = []
        ex: exif_mod.Exif | None = None
        if exif_block is not None:
            try:
                ex = exif_mod.parse_exif_block(exif_block)
            except exif_mod.ExifError as exc:
                notes.append(f'exif_parse_failed: {exc}')
        xp: xmp_mod.XmpPackage | None = None
        if xmp_block is not None:
            try:
                xp = xmp_mod.parse_xmp(xmp_block)
            except xmp_mod.XmpError as exc:
                notes.append(f'xmp_parse_failed: {exc}')
        collector = collect_fields(ex, xp, self.format)
        for note in notes:
            collector.note(note)
        return collector

    def _extract(self, path: str):
        collector = self._collector(path)
        return (collector.fields(), collector.warnings)

    def _clean(self, path: str, out_path=None):
        collector = self._collector(path)
        before = {item['key']: item for item in collector.fields()}
        warnings = list(collector.warnings)
        if out_path is None:
            warnings.append(f'{WARN_CLEAN_NO_OUT}: out_path не передан, очищенный файл не создан')
            return (collector.fields(), warnings)
        out = os.fspath(out_path)
        if os.path.abspath(out) == os.path.abspath(os.fspath(path)):
            raise ValueError('out_path совпадает с исходным файлом: оригинал не перезаписывается')
        with open(path, 'rb') as fh:
            data = fh.read()
        cleaned, removed = strip_metadata_segments(data)
        size_before = _frame_size(data)
        size_after = _frame_size(cleaned)
        if size_before is not None and size_after is not None and (size_before != size_after):
            raise JpegStructureError(f'frame size changed by clean: {size_before} -> {size_after}')
        _atomic_write(out, cleaned, mode_from=path)
        try:
            after = {item['key']: item for item in self._collector(out).fields()}
        except JpegStructureError as exc:
            try:
                os.unlink(out)
            except OSError:
                pass
            raise JpegStructureError(f'cleaned output is not a valid JPEG: {exc}') from exc
        fields = []
        still_present: list[str] = []
        for key, label in contract.fields_for(self.format):
            had = key in before
            has = key in after
            if had and has:
                fields.append(contract.field(key, label, contract.FOUND, after[key]['value']))
                still_present.append(key)
            elif had:
                fields.append(contract.field(key, label, contract.REMOVED, None))
            elif has:
                fields.append(contract.field(key, label, contract.FOUND, after[key]['value']))
                still_present.append(f'{key}(появилось после очистки)')
            else:
                fields.append(contract.field(key, label, contract.NOT_FOUND, None))
        if still_present:
            warnings.append(f"{WARN_CLEAN_PARTIAL}: после очистки в файле остались целевые поля {', '.join(still_present)}; вырезано сегментов: {(', '.join(removed) if removed else 'нет')}")
        return (fields, warnings)
