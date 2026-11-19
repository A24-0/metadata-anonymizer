from __future__ import annotations
import argparse
import json
import os
import sys
from typing import Sequence
from . import contract, registry

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m app', description='Метаданные файла по контракту «Кибер-ХиХ» (День 1: заглушки).')
    parser.add_argument('path', help='путь к файлу (jpg/png/pdf)')
    parser.add_argument('--clean', action='store_true', help='вернуть ответ режима очистки (файлы пока не изменяются: заглушка)')
    parser.add_argument('--out', default=None, help='куда писать очищенный файл (для --clean; заглушка ничего не пишет)')
    parser.add_argument('--pretty', action='store_true', help='отформатировать JSON')
    parser.add_argument('--strict', action='store_true', help='проверить ответ валидатором контракта и упасть при нарушении')
    return parser

def main(argv: Sequence[str] | None=None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.path):
        print(f'файл не найден: {args.path}', file=sys.stderr)
        return 2
    result = registry.clean(args.path, out_path=args.out) if args.clean else registry.read(args.path)
    if args.strict:
        try:
            contract.validate_result(result)
        except contract.ContractError as exc:
            print(f'contract violation: {exc}', file=sys.stderr)
            return 1
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    first = result['fields'][0]['status'] if result.get('fields') else None
    if first == contract.ERROR:
        return 2
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
