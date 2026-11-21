from __future__ import annotations
import logging
import os
import re
import tempfile
from typing import Any
import pikepdf
import pypdf
from . import contract
from .base import BaseHandler
from .registry import register
WARN_CLEAN_PARTIAL = 'clean_partial'
WARN_CLEAN_NO_OUT = 'clean_no_out_path'
INFO_FIELDS: tuple[tuple[str, str], ...] = (('author', '/Author'), ('creator', '/Creator'), ('producer', '/Producer'), ('created', '/CreationDate'), ('modified', '/ModDate'), ('title', '/Title'), ('subject', '/Subject'), ('keywords', '/Keywords'))
XMP_FIELDS: tuple[tuple[str, str], ...] = (('author', 'dc:creator'), ('creator', 'xmp:CreatorTool'), ('producer', 'pdf:Producer'), ('created', 'xmp:CreateDate'), ('modified', 'xmp:ModifyDate'), ('title', 'dc:title'), ('subject', 'dc:description'), ('keywords', 'pdf:Keywords'), ('xmp_creator_tool', 'xmp:CreatorTool'))
DATE_KEYS: frozenset[str] = frozenset({'created', 'modified'})
_PDF_DATE_RE = re.compile('^(?:D:)?(?P<y>\\d{4})(?P<mo>\\d{2})?(?P<d>\\d{2})?(?P<h>\\d{2})?(?P<mi>\\d{2})?(?P<s>\\d{2})?(?P<tz>.*)$')
_ISO_DATE_RE = re.compile('^\\d{4}-\\d{2}-\\d{2}([T ].*)?$')
_PDF_JUNK_RE = re.compile(b'%PDF-\\d\\.\\d|%[^\\r\\n]*|[\\s\\x00]+')
logging.getLogger('pypdf').setLevel(logging.ERROR)

def _as_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        parts = [_as_text(v) for v in value]
        return '; '.join((p for p in parts if p))
    if isinstance(value, dict):
        for v in value.values():
            text = _as_text(v)
            if text:
                return text
        return ''
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        for enc in ('utf-8', 'utf-16', 'latin-1'):
            try:
                return raw.decode(enc).replace('\x00', '').strip()
            except UnicodeDecodeError:
                continue
        return raw.decode('latin-1', 'replace').replace('\x00', '').strip()
    return str(value).replace('\x00', '').strip()

def _iso_date(value: Any) -> str:
    text = _as_text(value)
    if not text:
        return ''
    if _ISO_DATE_RE.match(text):
        return text
    m = _PDF_DATE_RE.match(text)
    if not m:
        return text
    y = m.group('y')
    mo = m.group('mo') or '01'
    d = m.group('d') or '01'
    h = m.group('h') or '00'
    mi = m.group('mi') or '00'
    s = m.group('s') or '00'
    out = f'{y}-{mo}-{d}T{h}:{mi}:{s}'
    tz = (m.group('tz') or '').strip()
    if tz.startswith('Z'):
        out += '+00:00'
    elif tz[:1] in '+-':
        digits = re.sub('\\D', '', tz[1:])
        hh = digits[0:2] or '00'
        mm = digits[2:4] or '00'
        out += f'{tz[0]}{hh}:{mm}'
    return out

def _field_value(key: str, raw: Any) -> str:
    return _iso_date(raw) if key in DATE_KEYS else _as_text(raw)

def _collect_name_tree(node: Any, out: list[tuple[str, Any]]) -> None:
    if node is None:
        return
    try:
        if '/Names' in node:
            arr = node['/Names']
            for i in range(0, len(arr) - 1, 2):
                out.append((_as_text(arr[i]), arr[i + 1]))
        for kid in node.get('/Kids', []) or []:
            _collect_name_tree(kid, out)
    except Exception:
        return

def _attachment_size(spec: Any) -> int | None:
    try:
        ef = spec.get('/EF') or {}
        stream = ef.get('/F') or ef.get('/UF')
    except Exception:
        return None
    if stream is None:
        return None
    try:
        params = stream.get('/Params') or {}
        size = params.get('/Size')
        if size is not None:
            return int(size)
    except Exception:
        pass
    try:
        return len(stream.read_bytes())
    except Exception:
        return None

def find_attachments(pdf: pikepdf.Pdf) -> list[dict[str, Any]]:
    found: list[tuple[str, Any, str]] = []
    try:
        root = pdf.Root
    except Exception:
        return []
    names_pairs: list[tuple[str, Any]] = []
    _collect_name_tree(root.get('/Names', {}).get('/EmbeddedFiles'), names_pairs)
    for name, spec in names_pairs:
        found.append((name, spec, '/Names/EmbeddedFiles'))

    def add_af(node: Any, where: str) -> None:
        try:
            af = node.get('/AF') if node is not None else None
        except Exception:
            return
        for spec in af or []:
            name = _as_text(spec.get('/UF') or spec.get('/F') or '') or '?'
            found.append((name, spec, where))
    add_af(root, '/AF(Root)')
    try:
        pages = list(pdf.pages)
    except Exception:
        pages = []
    for idx, page in enumerate(pages, start=1):
        add_af(page.obj, f'/AF(page {idx})')
    out: list[dict[str, Any]] = []
    seen_objgen: set[tuple[int, int]] = set()
    seen_fallback: set[tuple[str, int | None]] = set()
    for name, spec, where in found:
        try:
            objgen = spec.objgen
        except Exception:
            objgen = (0, 0)
        size = _attachment_size(spec)
        if objgen and objgen != (0, 0):
            if objgen in seen_objgen:
                for item in out:
                    if item['objgen'] == list(objgen) and where not in item['found_in']:
                        item['found_in'].append(where)
                continue
            seen_objgen.add(objgen)
        else:
            marker = (name, size)
            if marker in seen_fallback:
                continue
            seen_fallback.add(marker)
        out.append({'name': name, 'size_bytes': size, 'found_in': [where], 'objgen': [objgen[0], objgen[1]]})
    return out

def _is_structureless(data: bytes) -> bool:
    if b' obj' in data:
        return False
    return not _PDF_JUNK_RE.sub(b'', data)

def _read_bytes_head(path: str) -> bytes:
    with open(path, 'rb') as fh:
        return fh.read()

@register
class PdfHandler(BaseHandler):
    format = contract.FORMAT_PDF
    extensions = ('.pdf',)

    def _extract(self, path: str) -> tuple[list[dict[str, Any]], list[str]]:
        size = self.size_of(path)
        if size == 0:
            raise ValueError('empty file: 0 bytes')
        warnings: list[str] = []
        data = _read_bytes_head(path)
        try:
            reader = pypdf.PdfReader(path)
        except contract.ContractError:
            raise
        except Exception as exc:
            if _is_structureless(data):
                warnings.append('pdf_empty_document: в файле только сигнатура %PDF, косвенных объектов нет — метаданных нет')
                return (self._not_found_fields(), warnings)
            if b'/Encrypt' in data:
                raise ValueError(f'encrypted PDF: {type(exc).__name__}: {exc}') from exc
            raise exc
        if reader.is_encrypted:
            try:
                ok = reader.decrypt('')
            except Exception as exc:
                raise ValueError(f'encrypted PDF: {type(exc).__name__}: {exc}') from exc
            if not ok:
                raise ValueError('encrypted PDF: password required')
        info: dict[str, str] = {}
        try:
            md = reader.metadata
        except Exception as exc:
            raise ValueError(f'cannot read /Info: {exc}') from exc
        if md is not None:
            for key, info_key in INFO_FIELDS:
                try:
                    raw = md.get(info_key)
                except Exception:
                    continue
                value = _field_value(key, raw)
                if value:
                    info[key] = value
        xmp: dict[str, str] = {}
        attachments: list[dict[str, Any]] = []
        pdf = None
        try:
            pdf = pikepdf.open(path)
        except Exception as exc:
            warnings.append(f'xmp_not_read: {type(exc).__name__}: {exc}')
        if pdf is not None:
            with pdf:
                try:
                    with pdf.open_metadata() as meta:
                        for key, xmp_key in XMP_FIELDS:
                            try:
                                raw = meta.get(xmp_key)
                            except Exception:
                                continue
                            value = _field_value(key, raw)
                            if value and key not in xmp:
                                xmp[key] = value
                except Exception as exc:
                    warnings.append(f'xmp_not_read: {type(exc).__name__}: {exc}')
                try:
                    attachments = find_attachments(pdf)
                except Exception as exc:
                    warnings.append(f'attachments_not_read: {type(exc).__name__}: {exc}')
        fields = self._merge(info, xmp)
        warnings.extend(self._attachment_warnings(attachments))
        return (fields, warnings)

    def _not_found_fields(self) -> list[dict[str, Any]]:
        return [contract.field(k, lbl, contract.NOT_FOUND) for k, lbl in self.field_spec()]

    def _merge(self, info: dict[str, str], xmp: dict[str, str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, label in self.field_spec():
            info_val = info.get(key)
            xmp_val = xmp.get(key)
            if info_val and xmp_val:
                if info_val == xmp_val:
                    out.append(contract.field(key, label, contract.FOUND, info_val))
                else:
                    out.append(contract.field(key, label, contract.FOUND, f'/Info: {info_val} | XMP: {xmp_val}'))
            elif info_val or xmp_val:
                out.append(contract.field(key, label, contract.FOUND, info_val or xmp_val))
            else:
                out.append(contract.field(key, label, contract.NOT_FOUND))
        return out

    @staticmethod
    def _attachment_warnings(attachments: list[dict[str, Any]]) -> list[str]:
        if not attachments:
            return []
        parts = []
        for item in attachments:
            size = item['size_bytes']
            size_text = f'{size} bytes' if size is not None else 'size unknown'
            parts.append(f"{item['name']} ({size_text}) at {', '.join(item['found_in'])}")
        return [contract.WARN_PDF_EMBEDDED, 'pdf_attachments: ' + '; '.join(parts)]

    @staticmethod
    def _strip_metadata(pdf: pikepdf.Pdf) -> None:
        try:
            if '/Info' in pdf.trailer:
                del pdf.trailer['/Info']
        except Exception:
            pass
        try:
            if '/Info' in pdf.Root:
                del pdf.Root['/Info']
        except Exception:
            pass
        for obj in [pdf.Root, *[p.obj for p in pdf.pages]]:
            try:
                if '/Info' in obj:
                    del obj['/Info']
            except Exception:
                pass
        victims = []
        try:
            root = pdf.Root
            if '/Metadata' in root:
                victims.append(root)
        except Exception:
            pass
        try:
            for page in pdf.pages:
                if '/Metadata' in page.obj:
                    victims.append(page.obj)
        except Exception:
            pass
        try:
            for obj in pdf.objects:
                try:
                    if isinstance(obj, pikepdf.Dictionary) and '/Metadata' in obj:
                        victims.append(obj)
                except Exception:
                    continue
        except Exception:
            pass
        seen: set[tuple[int, int]] = set()
        for obj in victims:
            try:
                key = obj.objgen
            except Exception:
                key = (0, 0)
            if key in seen:
                continue
            seen.add(key)
            try:
                del obj['/Metadata']
            except Exception:
                continue

    @staticmethod
    def _residue(path: str) -> list[str]:
        residue: list[str] = []
        try:
            pdf = pikepdf.open(path)
        except Exception as exc:
            return [f'reopen_failed: {type(exc).__name__}: {exc}']
        with pdf:
            seen: set[tuple[int, int]] = set()
            try:
                if '/Info' in pdf.trailer:
                    residue.append('/Info in trailer')
                if '/Info' in pdf.Root:
                    residue.append('/Info in Root')
                    seen.add(pdf.Root.objgen)
                if '/Metadata' in pdf.Root:
                    residue.append('/Metadata in Root')
                    seen.add(pdf.Root.objgen)
                for idx, page in enumerate(pdf.pages, start=1):
                    if '/Metadata' in page.obj:
                        residue.append(f'/Metadata on page {idx}')
                        seen.add(page.obj.objgen)
                    if '/Info' in page.obj:
                        residue.append(f'/Info on page {idx}')
                        seen.add(page.obj.objgen)
                for obj in pdf.objects:
                    try:
                        if isinstance(obj, pikepdf.Dictionary) and '/Metadata' in obj:
                            if obj.objgen in seen:
                                continue
                            residue.append(f'/Metadata in object {obj.objgen}')
                    except Exception:
                        continue
            except Exception as exc:
                residue.append(f'residue_check_failed: {type(exc).__name__}: {exc}')
        return residue

    def _clean(self, path: str, out_path: str | os.PathLike[str] | None=None) -> tuple[list[dict[str, Any]], list[str]]:
        fields, warnings = self._extract(path)
        if out_path is None:
            warnings.append(f'{WARN_CLEAN_NO_OUT}: out_path не передан, очищенный файл не создан')
            return (fields, warnings)
        out = os.fspath(out_path)
        if os.path.abspath(out) == os.path.abspath(path):
            raise ValueError('out_path совпадает с исходным файлом: оригинал не перезаписывается')
        try:
            reader = pypdf.PdfReader(path)
        except Exception as exc:
            raise ValueError(f'cannot open PDF for cleaning: {type(exc).__name__}: {exc}') from exc
        if reader.is_encrypted:
            raise ValueError('encrypted PDF: password required, очистка невозможна')
        try:
            pdf = pikepdf.open(path)
        except Exception as exc:
            raise ValueError(f'cannot open PDF for cleaning: {type(exc).__name__}: {exc}') from exc
        directory = os.path.dirname(os.path.abspath(out)) or '.'
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.clean-', suffix='.tmp', dir=directory)
        os.close(fd)
        try:
            with pdf:
                self._strip_metadata(pdf)
                pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.disable, linearize=False, compress_streams=True)
            try:
                os.chmod(tmp, os.stat(path).st_mode & 511)
            except OSError:
                pass
            os.replace(tmp, out)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            after_fields, after_warnings = self._extract(out)
        except Exception as exc:
            try:
                os.unlink(out)
            except OSError:
                pass
            raise ValueError(f'cleaned output is not a readable PDF: {type(exc).__name__}: {exc}') from exc
        before = {item['key']: item for item in fields if item['status'] == contract.FOUND}
        after = {item['key']: item for item in after_fields if item['status'] == contract.FOUND}
        out_fields: list[dict[str, Any]] = []
        still: list[str] = []
        for key, label in self.field_spec():
            had = key in before
            has = key in after
            if had and has:
                out_fields.append(contract.field(key, label, contract.FOUND, after[key]['value']))
                still.append(key)
            elif had:
                out_fields.append(contract.field(key, label, contract.REMOVED))
            elif has:
                out_fields.append(contract.field(key, label, contract.FOUND, after[key]['value']))
                still.append(f'{key}(появилось после очистки)')
            else:
                out_fields.append(contract.field(key, label, contract.NOT_FOUND))
        warnings = list(after_warnings)
        residue = self._residue(out)
        if still:
            warnings.append(f"{WARN_CLEAN_PARTIAL}: после очистки остались целевые поля {', '.join(still)}")
        if residue:
            warnings.append(f'{WARN_CLEAN_PARTIAL}: в результате остались ключи метаданных: ' + ', '.join(residue))
        return (out_fields, warnings)
