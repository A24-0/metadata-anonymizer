from __future__ import annotations
import os
from typing import Any, Iterable
from . import contract
from .base import BaseHandler
from .contract import ContractError
__all__ = ['UnsupportedFormat', 'detect_format', 'get_handler', 'read', 'clean', 'register', 'describe', 'handlers']
SIGNATURES: tuple[tuple[str, bytes], ...] = ((contract.FORMAT_JPEG, b'\xff\xd8\xff'), (contract.FORMAT_PNG, b'\x89PNG\r\n\x1a\n'), (contract.FORMAT_PDF, b'%PDF-'))
SNIFF_BYTES = 1024
EXTENSION_MAP: dict[str, str] = {'.jpg': contract.FORMAT_JPEG, '.jpeg': contract.FORMAT_JPEG, '.jpe': contract.FORMAT_JPEG, '.jfif': contract.FORMAT_JPEG, '.png': contract.FORMAT_PNG, '.pdf': contract.FORMAT_PDF}
HANDLERS: dict[str, type[BaseHandler]] = {}

class UnsupportedFormat(ValueError):

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f'unsupported format: {path} ({reason})')

def _sniff(head: bytes) -> str | None:
    for fmt, sig in SIGNATURES:
        if head.startswith(sig):
            return fmt
    idx = head.find(b'%PDF-')
    if 0 <= idx < SNIFF_BYTES:
        return contract.FORMAT_PDF
    return None

def detect_format(path: str | os.PathLike[str]) -> str:
    p = os.fspath(path)
    try:
        with open(p, 'rb') as fh:
            head = fh.read(SNIFF_BYTES)
    except OSError as exc:
        raise UnsupportedFormat(p, f'cannot read: {exc.strerror or exc}') from exc
    fmt = _sniff(head)
    if fmt is not None:
        return fmt
    ext = os.path.splitext(p)[1].lower()
    fallback = EXTENSION_MAP.get(ext)
    if fallback is not None:
        return fallback
    detail = 'empty file' if not head else f'unknown signature {head[:8]!r}'
    raise UnsupportedFormat(p, f"{detail}, extension {ext or '<none>'!r}")

def register(handler: type[BaseHandler]) -> type[BaseHandler]:
    if not isinstance(handler, type) or not issubclass(handler, BaseHandler):
        raise TypeError('register() expects a BaseHandler subclass')
    fmt = handler.format
    if fmt not in contract.FIELD_SPECS:
        raise ContractError(f'handler {handler.__name__} declares unknown format {fmt!r}')
    if fmt in HANDLERS and HANDLERS[fmt] is not handler:
        raise ContractError(f'format {fmt!r} already registered by {HANDLERS[fmt].__name__}')
    for ext in handler.extensions:
        EXTENSION_MAP.setdefault(ext.lower(), fmt)
    HANDLERS[fmt] = handler
    return handler

def get_handler(path: str | os.PathLike[str]) -> BaseHandler:
    fmt = detect_format(path)
    try:
        cls = HANDLERS[fmt]
    except KeyError:
        raise UnsupportedFormat(os.fspath(path), f'no handler registered for {fmt}') from None
    return cls()

def read(path: str | os.PathLike[str]) -> dict[str, Any]:
    p = os.fspath(path)
    try:
        handler = get_handler(p)
    except UnsupportedFormat as exc:
        return contract.error_result(os.path.basename(p), _guess_format_for_error(p), _safe_size(p), str(exc), contract.ERROR_UNSUPPORTED_FORMAT)
    return handler.read(p)

def clean(path: str | os.PathLike[str], out_path: str | os.PathLike[str] | None=None) -> dict[str, Any]:
    p = os.fspath(path)
    try:
        handler = get_handler(p)
    except UnsupportedFormat as exc:
        return contract.error_result(os.path.basename(p), _guess_format_for_error(p), _safe_size(p), str(exc), contract.ERROR_UNSUPPORTED_FORMAT)
    return handler.clean(p, out_path=out_path)

def _guess_format_for_error(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return EXTENSION_MAP.get(ext, contract.FORMAT_JPEG)

def _safe_size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0

def describe() -> dict[str, Any]:
    return {'formats': {fmt: {'handler': HANDLERS[fmt].__name__ if fmt in HANDLERS else None, 'extensions': sorted((e for e, f in EXTENSION_MAP.items() if f == fmt)), 'fields': [{'key': k, 'label': lbl} for k, lbl in contract.FIELD_SPECS[fmt]]} for fmt in contract.FORMATS}, 'statuses': sorted(contract.STATUSES), 'signatures': {fmt: sig.hex() for fmt, sig in SIGNATURES}}

def handlers() -> Iterable[type[BaseHandler]]:
    return tuple(HANDLERS.values())
