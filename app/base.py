from __future__ import annotations
import os
from typing import Any, Iterable, Mapping
from . import contract

class BaseHandler:
    format: str = ''
    extensions: tuple[str, ...] = ()

    @classmethod
    def field_spec(cls) -> tuple[tuple[str, str], ...]:
        return contract.fields_for(cls.format)

    def name_of(self, path: str) -> str:
        return os.path.basename(path)

    def size_of(self, path: str) -> int:
        try:
            return os.stat(path).st_size
        except OSError:
            return 0

    def read(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        p = os.fspath(path)
        name, size = (self.name_of(p), self.size_of(p))
        try:
            fields, warnings = self._extract(p)
            return contract.build_result(name, self.format, size, fields, warnings)
        except contract.ContractError:
            raise
        except Exception as exc:
            return contract.error_result(name, self.format, size, self._describe_exc(exc))

    def clean(self, path: str | os.PathLike[str], out_path: str | os.PathLike[str] | None=None) -> dict[str, Any]:
        p = os.fspath(path)
        name, size = (self.name_of(p), self.size_of(p))
        try:
            fields, warnings = self._clean(p, out_path)
            return contract.build_result(name, self.format, size, fields, warnings)
        except contract.ContractError:
            raise
        except Exception as exc:
            return contract.error_result(name, self.format, size, self._describe_exc(exc))

    def _extract(self, path: str) -> tuple[Iterable[Mapping[str, Any]], list[str]]:
        raise NotImplementedError(f'{type(self).__name__}._extract is not implemented')

    def _clean(self, path: str, out_path: str | os.PathLike[str] | None) -> tuple[Iterable[Mapping[str, Any]], list[str]]:
        raise NotImplementedError(f'{type(self).__name__}._clean is not implemented')

    @staticmethod
    def _describe_exc(exc: BaseException) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        return f'{exc.__class__.__name__}: {text}' if exc.__class__.__name__ not in text else text

class StubHandler(BaseHandler):
    stub_note: str = 'reader not implemented in wave 1'
    stub_values: dict[str, str | None] = {}
    stub_warnings: tuple[str, ...] = ()

    def _stub_fields(self) -> list[dict[str, Any]]:
        out = []
        for key, label in self.field_spec():
            value = self.stub_values.get(key)
            if value is None:
                out.append(contract.field(key, label, contract.NOT_FOUND))
            else:
                out.append(contract.field(key, label, contract.FOUND, value))
        return out

    def _stub_warnings(self) -> list[str]:
        return list(self.stub_warnings) or [f'{contract.WARN_STUB_PREFIX} {self.format}: {self.stub_note}']

    def _extract(self, path: str) -> tuple[list[dict[str, Any]], list[str]]:
        return (self._stub_fields(), self._stub_warnings())

    def _clean(self, path: str, out_path: str | os.PathLike[str] | None=None) -> tuple[list[dict[str, Any]], list[str]]:
        out = []
        for item in self._stub_fields():
            if item['status'] == contract.FOUND:
                out.append({**item, 'status': contract.REMOVED, 'value': None})
            else:
                out.append(item)
        warnings = self._stub_warnings() + [f'{contract.WARN_STUB_PREFIX} clean: файл не изменён, out_path={(os.fspath(out_path) if out_path else None)!r}']
        return (out, warnings)
