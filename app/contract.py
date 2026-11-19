from __future__ import annotations
from typing import Any, Iterable, Mapping, Sequence
FOUND = 'found'
NOT_FOUND = 'not_found'
REMOVED = 'removed'
NOT_SUPPORTED = 'not_supported'
ERROR = 'error'
STATUSES: frozenset[str] = frozenset({FOUND, NOT_FOUND, REMOVED, NOT_SUPPORTED, ERROR})
FORMAT_JPEG = 'jpeg'
FORMAT_PNG = 'png'
FORMAT_PDF = 'pdf'
FORMATS: tuple[str, ...] = (FORMAT_JPEG, FORMAT_PNG, FORMAT_PDF)
IMAGE_FIELDS: tuple[tuple[str, str], ...] = (('author', 'Автор'), ('software', 'Программа'), ('gps', 'Геометка'), ('date_taken', 'Дата съёмки'), ('date_modified', 'Дата изменения'), ('device_make', 'Производитель устройства'), ('device_model', 'Модель устройства'), ('serial', 'Серийный номер'), ('copyright', 'Правообладатель'), ('description', 'Описание'), ('xmp_history', 'История изменений XMP'))
PDF_FIELDS: tuple[tuple[str, str], ...] = (('author', 'Автор'), ('creator', 'Создатель'), ('producer', 'Производитель файла'), ('created', 'Дата создания'), ('modified', 'Дата изменения'), ('title', 'Заголовок'), ('subject', 'Тема'), ('keywords', 'Ключевые слова'), ('xmp_creator_tool', 'Инструмент XMP'))
FIELD_SPECS: dict[str, tuple[tuple[str, str], ...]] = {FORMAT_JPEG: IMAGE_FIELDS, FORMAT_PNG: IMAGE_FIELDS, FORMAT_PDF: PDF_FIELDS}
FIELD_SOURCES: dict[str, dict[str, str]] = {FORMAT_JPEG: {'author': 'EXIF IFD0 Artist / XMP dc:creator', 'software': 'EXIF IFD0 Software / XMP xmp:CreatorTool', 'gps': 'EXIF GPS-IFD (0x8825) / XMP exif:GPS*', 'date_taken': 'EXIF Exif-IFD (0x8769) DateTimeOriginal', 'date_modified': 'EXIF IFD0 DateTime', 'device_make': 'EXIF IFD0 Make', 'device_model': 'EXIF IFD0 Model', 'serial': 'EXIF Exif-IFD BodySerialNumber', 'copyright': 'EXIF IFD0 Copyright / XMP dc:rights', 'description': 'EXIF IFD0 ImageDescription / XMP dc:description', 'xmp_history': 'XMP xmpMM:History (APP1 xap/1.0)'}, FORMAT_PNG: {'author': 'tEXt/zTXt/iTXt Author / eXIf Artist', 'software': 'tEXt/iTXt Software / eXIf Software', 'gps': 'eXIf GPS-IFD', 'date_taken': 'eXIf DateTimeOriginal', 'date_modified': 'tIME / eXIf DateTime', 'device_make': 'eXIf Make', 'device_model': 'eXIf Model', 'serial': 'eXIf BodySerialNumber', 'copyright': 'tEXt/iTXt Copyright', 'description': 'tEXt/iTXt Description', 'xmp_history': 'iTXt XML:com.adobe.xmp → xmpMM:History'}, FORMAT_PDF: {'author': '/Info /Author, XMP dc:creator', 'creator': '/Info /Creator, XMP xmp:CreatorTool', 'producer': '/Info /Producer, XMP pdf:Producer', 'created': '/Info /CreationDate, XMP xmp:CreateDate', 'modified': '/Info /ModDate, XMP xmp:ModifyDate', 'title': '/Info /Title, XMP dc:title', 'subject': '/Info /Subject, XMP dc:description', 'keywords': '/Info /Keywords, XMP pdf:Keywords', 'xmp_creator_tool': 'XMP xmp:CreatorTool'}}
WARN_PDF_EMBEDDED = 'pdf_has_embedded_files_not_cleaned'
WARN_STUB_PREFIX = 'stub:'
ERROR_UNSUPPORTED_FORMAT = 'unsupported_format'
ERROR_UNREADABLE_FILE = 'unreadable_file'
ERROR_PARSE_FAILED = 'parse_failed'
ERROR_CONTRACT_VIOLATION = 'contract_violation'

class ContractError(ValueError):
    pass

def fields_for(fmt: str) -> tuple[tuple[str, str], ...]:
    try:
        return FIELD_SPECS[fmt]
    except KeyError:
        raise ContractError(f'unknown format: {fmt!r}') from None

def field_spec(fmt: str) -> dict[str, str]:
    return dict(fields_for(fmt))

def field(key: str, label: str, status: str, value: str | None=None) -> dict[str, Any]:
    if status not in STATUSES:
        raise ContractError(f'unknown status {status!r} for field {key!r}')
    return {'key': key, 'label': label, 'status': status, 'value': value}

def build_result(name: str, fmt: str, size_bytes: int, fields: Iterable[Mapping[str, Any]]=(), warnings: Iterable[str]=()) -> dict[str, Any]:
    spec = fields_for(fmt)
    provided: dict[str, Mapping[str, Any]] = {}
    for item in fields:
        key = item.get('key')
        if key not in dict(spec):
            raise ContractError(f'unknown field key {key!r} for format {fmt!r}')
        if key in provided:
            raise ContractError(f'duplicate field {key!r}')
        provided[key] = item
    out: list[dict[str, Any]] = []
    for key, label in spec:
        item = provided.get(key)
        if item is None:
            out.append(field(key, label, NOT_FOUND))
            continue
        status = item.get('status')
        if status not in STATUSES:
            raise ContractError(f'unknown status {status!r} for field {key!r}')
        value = item.get('value')
        if status == FOUND and (not value):
            raise ContractError(f'field {key!r} has status=found without value')
        if status != FOUND and value is not None:
            raise ContractError(f'field {key!r} has status={status} with value')
        out.append({'key': key, 'label': label, 'status': status, 'value': value})
    return {'file': {'name': name, 'format': fmt, 'size_bytes': size_bytes}, 'fields': out, 'warnings': list(warnings)}

def error_result(name: str, fmt: str, size_bytes: int, message: str, code: str=ERROR_PARSE_FAILED) -> dict[str, Any]:
    warnings = [f'{code}: {message}']
    fields = [{'key': key, 'label': label, 'status': ERROR, 'value': None} for key, label in fields_for(fmt)]
    return {'file': {'name': name, 'format': fmt, 'size_bytes': size_bytes}, 'fields': fields, 'warnings': warnings}

def validate_result(result: Any) -> None:
    if not isinstance(result, dict):
        raise ContractError(f'result must be dict, got {type(result).__name__}')
    extra = set(result) - {'file', 'fields', 'warnings'}
    if extra:
        raise ContractError(f'unexpected top-level keys: {sorted(extra)}')
    for key in ('file', 'fields', 'warnings'):
        if key not in result:
            raise ContractError(f'missing top-level key {key!r}')
    file_info = result['file']
    if not isinstance(file_info, dict):
        raise ContractError('file must be an object')
    fkeys = set(file_info)
    if fkeys != {'name', 'format', 'size_bytes'}:
        raise ContractError(f'file keys must be name/format/size_bytes, got {sorted(fkeys)}')
    if not isinstance(file_info['name'], str) or not file_info['name']:
        raise ContractError('file.name must be a non-empty string')
    fmt = file_info['format']
    if fmt not in FIELD_SPECS:
        raise ContractError(f'file.format must be one of {list(FORMATS)}, got {fmt!r}')
    size = file_info['size_bytes']
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ContractError(f'file.size_bytes must be int >= 0, got {size!r}')
    if not isinstance(result['warnings'], list):
        raise ContractError('warnings must be a list')
    for w in result['warnings']:
        if not isinstance(w, str) or not w:
            raise ContractError(f'warnings items must be non-empty strings, got {w!r}')
    fields = result['fields']
    if not isinstance(fields, list):
        raise ContractError('fields must be a list')
    spec = fields_for(fmt)
    expected_keys = [k for k, _ in spec]
    got_keys = [f.get('key') if isinstance(f, dict) else None for f in fields]
    missing = [k for k in expected_keys if k not in got_keys]
    if missing:
        raise ContractError(f'missing fields for {fmt}: {missing}')
    if got_keys != expected_keys:
        raise ContractError(f'fields must be exactly {expected_keys} in that order, got {got_keys}')
    labels = dict(spec)
    for item in fields:
        if not isinstance(item, dict):
            raise ContractError(f'field must be an object, got {type(item).__name__}')
        if set(item) != {'key', 'label', 'status', 'value'}:
            raise ContractError(f'field keys must be key/label/status/value, got {sorted(item)}')
        key, label, status, value = (item['key'], item['label'], item['status'], item['value'])
        if label != labels[key]:
            raise ContractError(f'label mismatch for {key!r}: {label!r} != {labels[key]!r}')
        if status not in STATUSES:
            raise ContractError(f'unknown status {status!r} for field {key!r}')
        if status == FOUND:
            if not isinstance(value, str) or not value:
                raise ContractError(f'field {key!r} status=found requires non-empty string value')
        elif value is not None:
            raise ContractError(f'field {key!r} status={status} must have value=None')
