from __future__ import annotations
from . import contract
from .contract import ContractError, ERROR, FOUND, NOT_FOUND, NOT_SUPPORTED, REMOVED, STATUSES, build_result, error_result, validate_result
from .registry import UnsupportedFormat, clean, describe, detect_format, get_handler, read, register
from . import jpeg
from . import pdf
from . import png
__version__ = '0.1.0'
__all__ = ['read', 'clean', 'detect_format', 'describe', 'get_handler', 'register', 'UnsupportedFormat', 'contract', 'build_result', 'error_result', 'validate_result', 'ContractError', 'FOUND', 'NOT_FOUND', 'REMOVED', 'NOT_SUPPORTED', 'ERROR', 'STATUSES', '__version__']
