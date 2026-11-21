from __future__ import annotations
import os
import tempfile
import zlib
from . import contract
from . import exif as exif_mod
from . import xmp as xmp_mod
from .base import BaseHandler
from .fields import FieldCollector
from .jpeg import WARN_CLEAN_NO_OUT, WARN_CLEAN_PARTIAL, collect_fields
from .registry import register
__all__ = ['PngHandler', 'PngStructureError', 'read_chunks', 'decode_text_chunk', 'strip_metadata_chunks', 'chunk_types', 'PNG_SIGNATURE', 'XMP_KEYWORD', 'KEPT_CHUNKS', 'STRIPPED_CHUNKS']
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
XMP_KEYWORD = 'xml:com.adobe.xmp'
TEXT_CHUNKS = {b'tEXt', b'zTXt', b'iTXt'}
KEPT_CHUNKS = frozenset({b'IHDR', b'PLTE', b'tRNS', b'IDAT', b'IEND', b'gAMA', b'sRGB', b'cHRM', b'iCCP', b'sBIT', b'bKGD', b'pHYs', b'acTL', b'fcTL', b'fdAT'})
STRIPPED_CHUNKS = frozenset({b'tEXt', b'zTXt', b'iTXt', b'eXIf', b'tIME'})
KEYWORD_FIELDS: dict[str, str] = {'author': 'author', 'artist': 'author', 'xpauthor': 'author', 'software': 'software', 'creatortool': 'software', 'copyright': 'copyright', 'description': 'description', 'comment': 'description', 'creation time': 'date_modified', 'creationtime': 'date_modified', 'creation_time': 'date_modified', 'date:create': 'date_modified'}

class PngStructureError(ValueError):
    pass

def _chunk_name(ctype: bytes) -> str:
    return ctype.decode('latin-1', 'replace')

def _scan_chunks(data: bytes) -> list[tuple[bytes, int, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise PngStructureError(f"no PNG signature: first bytes are {data[:8].hex() or '<empty>'}")
    chunks: list[tuple[bytes, int, bytes]] = []
    n = len(data)
    i = 8
    ended = False
    while i + 8 <= n:
        length = int.from_bytes(data[i:i + 4], 'big')
        ctype = data[i + 4:i + 8]
        body_end = i + 8 + length
        if body_end + 4 > n:
            raise PngStructureError(f'truncated chunk {_chunk_name(ctype)!r} at offset {i}: declares {length} bytes, file has {n - i - 8}')
        body = data[i + 8:body_end]
        stored = int.from_bytes(data[body_end:body_end + 4], 'big')
        actual = zlib.crc32(ctype + body) & 4294967295
        if stored != actual:
            raise PngStructureError(f'CRC mismatch in chunk {_chunk_name(ctype)!r} at offset {i}: stored {stored:08x}, computed {actual:08x}')
        chunks.append((ctype, i, body))
        if ctype == b'IEND':
            ended = True
            break
        i = body_end + 4
    if not chunks or chunks[0][0] != b'IHDR':
        raise PngStructureError('no IHDR chunk: not a valid PNG stream')
    if len(chunks[0][2]) != 13:
        raise PngStructureError(f'IHDR chunk declares {len(chunks[0][2])} bytes, expected 13')
    if not ended:
        raise PngStructureError('no IEND chunk: file truncated')
    if not any((ctype == b'IDAT' for ctype, _, _ in chunks)):
        raise PngStructureError('no IDAT chunk: image data missing')
    return chunks

def read_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    return [(ctype, body) for ctype, _, body in _scan_chunks(data)]

def chunk_types(data: bytes) -> list[str]:
    return [_chunk_name(ctype) for ctype, _, _ in _scan_chunks(data)]

def _text_bytes(raw: bytes) -> str:
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1', 'replace')

def decode_text_chunk(ctype: bytes, body: bytes) -> tuple[str, str] | None:
    if ctype == b'tEXt':
        keyword, sep, text = body.partition(b'\x00')
        if not sep:
            raise ValueError('tEXt without NUL separator')
        return (keyword.decode('latin-1', 'replace'), _text_bytes(text))
    if ctype == b'zTXt':
        keyword, sep, rest = body.partition(b'\x00')
        if not sep or len(rest) < 2:
            raise ValueError('zTXt without NUL separator or compression header')
        method, payload = (rest[0], rest[1:])
        if method != 0:
            raise ValueError(f'zTXt unknown compression method {method}')
        text = _text_bytes(zlib.decompress(payload))
        return (keyword.decode('latin-1', 'replace'), text)
    if ctype == b'iTXt':
        keyword, sep, rest = body.partition(b'\x00')
        if not sep or len(rest) < 2:
            raise ValueError('iTXt without NUL separator or compression header')
        flag, method, rest = (rest[0], rest[1], rest[2:])
        lang, sep1, rest = rest.partition(b'\x00')
        translated, sep2, text = rest.partition(b'\x00')
        if not sep1 or not sep2:
            raise ValueError('iTXt without language/translated-keyword fields')
        if flag == 1:
            if method != 0:
                raise ValueError(f'iTXt unknown compression method {method}')
            text = zlib.decompress(text)
        elif flag != 0:
            raise ValueError(f'iTXt unknown compression flag {flag}')
        return (keyword.decode('latin-1', 'replace'), text.decode('utf-8', 'replace'))
    raise ValueError(f'not a text chunk: {ctype!r}')

def _keyword_key(keyword: str) -> str:
    return ' '.join(keyword.strip().lower().replace('_', ' ').split())

def _time_value(body: bytes) -> str | None:
    if len(body) != 7:
        return None
    year = int.from_bytes(body[0:2], 'big')
    month, day, hour, minute, second = body[2:7]
    return f'{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}'

def _image_size(data: bytes) -> tuple[int, int] | None:
    try:
        chunks = _scan_chunks(data)
    except PngStructureError:
        return None
    body = chunks[0][2]
    return (int.from_bytes(body[0:4], 'big'), int.from_bytes(body[4:8], 'big'))

def _chunk_label(ctype: bytes, body: bytes) -> str:
    name = _chunk_name(ctype)
    if ctype in TEXT_CHUNKS:
        keyword = body.split(b'\x00', 1)[0].decode('latin-1', 'replace').strip()
        return f'{name}({keyword})' if keyword else name
    return name

def strip_metadata_chunks(data: bytes) -> tuple[bytes, list[str]]:
    chunks = _scan_chunks(data)
    out = bytearray(PNG_SIGNATURE)
    removed: list[str] = []
    n = len(data)
    for ctype, offset, body in chunks:
        end = offset + 8 + len(body) + 4
        if end > n:
            raise PngStructureError(f'truncated chunk {_chunk_name(ctype)!r} at offset {offset}')
        if ctype in KEPT_CHUNKS:
            out += data[offset:end]
        else:
            removed.append(_chunk_label(ctype, body))
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

@register
class PngHandler(BaseHandler):
    format = contract.FORMAT_PNG
    extensions = ('.png',)

    def _collector(self, path: str) -> FieldCollector:
        with open(path, 'rb') as fh:
            data = fh.read()
        chunks = read_chunks(data)
        notes: list[str] = []
        collector = FieldCollector(self.format)
        exif_block: bytes | None = None
        xmp_block: bytes | None = None
        time_text: str | None = None
        for ctype, body in chunks:
            if ctype in TEXT_CHUNKS:
                try:
                    parsed = decode_text_chunk(ctype, body)
                except (ValueError, zlib.error) as exc:
                    notes.append(f'png_text_chunk_skipped: {exc}')
                    continue
                if parsed is None:
                    continue
                keyword, text = parsed
                if _keyword_key(keyword) == XMP_KEYWORD:
                    if xmp_block is None:
                        xmp_block = text.encode('utf-8', 'replace')
                    continue
                field_key = KEYWORD_FIELDS.get(_keyword_key(keyword))
                if field_key is None:
                    continue
                if field_key.startswith('date_'):
                    collector.add_date(field_key, text, 'PNG')
                else:
                    collector.add(field_key, text, 'PNG')
            elif ctype == b'eXIf' and exif_block is None:
                exif_block = body
            elif ctype == b'tIME' and time_text is None:
                time_text = _time_value(body)
                if time_text is None:
                    notes.append(f'png_time_chunk_skipped: expected 7 bytes, got {len(body)}')
        if time_text:
            collector.add_date('date_modified', time_text, 'PNG tIME')
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
        collect_fields(ex, xp, self.format, collector=collector, exif_source='eXIf')
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
        cleaned, removed = strip_metadata_chunks(data)
        size_before = _image_size(data)
        size_after = _image_size(cleaned)
        if size_before is not None and size_after is not None and (size_before != size_after):
            raise PngStructureError(f'image size changed by clean: {size_before} -> {size_after}')
        _atomic_write(out, cleaned, mode_from=path)
        try:
            after = {item['key']: item for item in self._collector(out).fields()}
        except PngStructureError as exc:
            try:
                os.unlink(out)
            except OSError:
                pass
            raise PngStructureError(f'cleaned output is not a valid PNG: {exc}') from exc
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
            warnings.append(f"{WARN_CLEAN_PARTIAL}: после очистки в файле остались целевые поля {', '.join(still_present)}; вырезано чанков: {(', '.join(removed) if removed else 'нет')}")
        return (fields, warnings)
