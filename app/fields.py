from __future__ import annotations
import os
import re
from typing import Any, Iterable, Mapping, Sequence
from . import contract
__all__ = ['FieldCollector', 'normalize_date', 'date_token', 'clean_plan', 'WARN_NOT_WRITTEN']
WARN_NOT_WRITTEN = 'file_not_written_day1'
DATE_RE = re.compile('^(?P<y>\\d{4})[:\\-](?P<mo>\\d{2})[:\\-](?P<d>\\d{2})(?:[ T](?P<h>\\d{2}):(?P<mi>\\d{2}):(?P<s>\\d{2})(?P<frac>\\.\\d+)?(?P<tz>Z|[+-]\\d{2}:?\\d{2})?)?$')

def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = DATE_RE.match(text)
    if not match:
        return text
    parts = match.groupdict()
    out = f"{parts['y']}-{parts['mo']}-{parts['d']}"
    if parts['h'] is not None:
        out += f"T{parts['h']}:{parts['mi']}:{parts['s']}{parts['frac'] or ''}"
        tz = parts['tz']
        if tz:
            if tz == 'Z':
                out += 'Z'
            else:
                digits = tz.replace(':', '')
                out += f'{digits[:3]}:{digits[3:]}'
    return out

def date_token(value: Any) -> str:
    text = normalize_date(value) or ''
    return re.sub('\\D', '', text)

class FieldCollector:

    def __init__(self, fmt: str) -> None:
        self.format = fmt
        self.labels = dict(contract.fields_for(fmt))
        self._items: dict[str, list[tuple[str, str, str]]] = {}
        self.warnings: list[str] = []

    def add(self, key: str, value: Any, source: str, token: str | None=None) -> bool:
        if key not in self.labels:
            raise contract.ContractError(f'unknown field key {key!r} for {self.format!r}')
        if value is None:
            return False
        text = str(value).strip()
        if not text:
            self.warnings.append(f'empty_value: {key} ({source}) — тег присутствует, значение пустое')
            return False
        self._items.setdefault(key, []).append((source, text, token or text))
        return True

    def add_date(self, key: str, value: Any, source: str) -> bool:
        return self.add(key, normalize_date(value), source, token=date_token(value))

    def note(self, warning: str) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)

    def fields(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, label in contract.fields_for(self.format):
            items = self._items.get(key)
            if not items:
                continue
            out.append(contract.field(key, label, contract.FOUND, self._merge(key, items)))
        return out

    def _merge(self, key: str, items: Sequence[tuple[str, str, str]]) -> str:
        first = items[0]
        if len(items) == 1:
            return first[1]
        tokens = {_token_of(item[2], key) for item in items}
        if len(tokens) == 1:
            return first[1]
        detail = '; '.join((f'{src}: {value}' for src, value, _ in items))
        if key == 'gps':
            self.warnings.append(f'metadata_conflict: {key}: {detail}')
            return detail
        self.warnings.append(f'metadata_conflict: {key}: {detail}; used={first[0]}')
        return first[1]

def _token_of(token: str, key: str) -> str:
    if key == 'gps':
        numbers = re.findall('-?\\d+(?:[.,]\\d+)?', token)
        return '|'.join((f"{float(n.replace(',', '.')):.7f}" for n in numbers))
    if key.startswith('date_'):
        return date_token(token)
    return ' '.join(token.split()).casefold() or token

def clean_plan(fields: Iterable[Mapping[str, Any]], warnings: Iterable[str], out_path: str | os.PathLike[str] | None=None) -> tuple[list[dict[str, Any]], list[str]]:
    plan: list[dict[str, Any]] = []
    would_remove = 0
    kept = 0
    for item in fields:
        if item.get('status') == contract.FOUND:
            plan.append({**item, 'status': contract.REMOVED, 'value': None})
            would_remove += 1
        else:
            plan.append(dict(item))
            kept += 1
    notes = [WARN_NOT_WRITTEN]
    notes.extend(warnings)
    notes.append(f'clean_plan: would_remove={would_remove}, unchanged={kept}, out_path={(os.fspath(out_path) if out_path else None)!r}; файл не перезаписан (запись и CRC — Дни 2-7)')
    return (plan, notes)
