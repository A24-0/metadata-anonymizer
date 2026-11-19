from __future__ import annotations
import zlib
from . import contract
from . import exif as exif_mod
from . import xmp as xmp_mod
from .base import BaseHandler
from .fields import FieldCollector, clean_plan
from .jpeg import collect_fields
from .registry import register
__all__ = ['PngHandler', 'PngStructureError', 'read_chunks', 'decode_text_chunk', 'PNG_SIGNATURE', 'XMP_KEYWORD']
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
XMP_KEYWORD = 'xml:com.adobe.xmp'
TEXT_CHUNKS = {b'tEXt', b'zTXt', b'iTXt'}
KEYWORD_FIELDS: dict[str, str] = {'author': 'author', 'artist': 'author', 'xpauthor': 'author', 'software': 'software', 'creatortool': 'software', 'copyright': 'copyright', 'description': 'description', 'comment': 'description', 'creation time': 'date_modified', 'creationtime': 'date_modified', 'creation_time': 'date_modified', 'date:create': 'date_modified'}

class PngStructureError(ValueError):
    pass

def read_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise PngStructureError(f"no PNG signature: first bytes are {data[:8].hex() or '<empty>'}")
    chunks: list[tuple[bytes, bytes]] = []
    seen_ihdr = False
    ended = False
    n = len(data)
    i = 8
    while i + 8 <= n:
        length = int.from_bytes(data[i:i + 4], 'big')
        ctype = data[i + 4:i + 8]
        body_end = i + 8 + length
        if body_end + 4 > n:
            raise PngStructureError(f"truncated chunk {ctype.decode('latin-1', 'replace')!r} at offset {i}: declares {length} bytes, file has {n - i - 8}")
        chunks.append((ctype, data[i + 8:body_end]))
        if ctype == b'IHDR':
            seen_ihdr = True
        if ctype == b'IEND':
            ended = True
            break
        i = body_end + 4
    if not seen_ihdr:
        raise PngStructureError('no IHDR chunk: not a valid PNG stream')
    if not ended:
        raise PngStructureError('no IEND chunk: file truncated')
    return chunks

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
        return clean_plan(collector.fields(), collector.warnings, out_path)
