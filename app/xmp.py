from __future__ import annotations
import xml.etree.ElementTree as ET
from typing import Any, Mapping
__all__ = ['XmpError', 'XmpPackage', 'parse_xmp', 'render_history', 'XMP_NS', 'ns_uri']
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
XMP_NS: dict[str, str] = {'dc': 'http://purl.org/dc/elements/1.1/', 'xmp': 'http://ns.adobe.com/xap/1.0/', 'xmpMM': 'http://ns.adobe.com/xap/1.0/mm/', 'xmpRights': 'http://ns.adobe.com/xap/1.0/rights/', 'exif': 'http://ns.adobe.com/exif/1.0/', 'tiff': 'http://ns.adobe.com/tiff/1.0/', 'dcterms': 'http://purl.org/dc/terms/', 'photoshop': 'http://ns.adobe.com/photoshop/1.0/', 'stEvt': 'http://ns.adobe.com/xap/1.0/sType/ResourceEvent#', 'stRef': 'http://ns.adobe.com/xap/1.0/sType/ResourceRef#'}

class XmpError(ValueError):
    pass

class XmpPackage:

    def __init__(self, values: Mapping[tuple[str, str], Any], toolkit: str | None=None):
        self.values: dict[tuple[str, str], Any] = dict(values)
        self.toolkit = toolkit

    def get(self, prefix: str, name: str) -> Any:
        return self.values.get((ns_uri(prefix), name))

    def text(self, prefix: str, name: str) -> str | None:
        value = self.get(prefix, name)
        for item in _flatten(value):
            text = str(item).strip()
            if text:
                return text
        return None

    def texts(self, prefix: str, name: str) -> list[str]:
        value = self.get(prefix, name)
        out: list[str] = []
        for item in _flatten(value):
            text = str(item).strip()
            if text:
                out.append(text)
        return out

    def has(self, prefix: str, name: str) -> bool:
        return (ns_uri(prefix), name) in self.values

    def history(self) -> str | None:
        return render_history(self.get('xmpMM', 'History'))

    def __bool__(self) -> bool:
        return bool(self.values)

    def __repr__(self) -> str:
        return f'<XmpPackage {len(self.values)} properties>'

def ns_uri(prefix: str) -> str:
    try:
        return XMP_NS[prefix]
    except KeyError:
        raise XmpError(f'unknown XMP prefix {prefix!r}') from None

def parse_xmp(data: bytes | str) -> XmpPackage:
    if not data:
        raise XmpError('empty XMP packet')
    if isinstance(data, str):
        data = data.encode('utf-8', 'replace')
    start = data.find(b'<')
    end = data.rfind(b'>')
    if start < 0 or end <= start:
        raise XmpError('no XML markup found in XMP packet')
    payload = data[start:end + 1]
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise XmpError(f'XMP XML parse error: {exc}') from exc
    values: dict[tuple[str, str], Any] = {}
    descriptions = [el for el in root.iter() if el.tag == _q(RDF, 'Description')]
    if not descriptions and root.tag != _q(RDF, 'RDF'):
        descriptions = [root] if root.tag.startswith('{') else []
    toolkit = None
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.endswith('}xmpmeta'):
            toolkit = el.get('x:xmptk')
    for desc in descriptions:
        for key, value in desc.attrib.items():
            if not key.startswith('{'):
                continue
            uri, local = _split_tag(key)
            if uri == RDF:
                continue
            values[uri, local] = value
        for child in desc:
            if not isinstance(child.tag, str) or not child.tag.startswith('{'):
                continue
            uri, local = _split_tag(child.tag)
            if uri == RDF:
                continue
            values[uri, local] = _element_value(child)
    if not values:
        raise XmpError('XMP packet contains no properties')
    return XmpPackage(values, toolkit)

def render_history(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    items = value if isinstance(value, (list, tuple)) else [value]
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            fields = [f'{k}={v}' for k, v in item.items() if str(v).strip()]
            if fields:
                parts.append(', '.join(fields))
        else:
            text = str(item).strip()
            if text:
                parts.append(text)
    return ' | '.join(parts) if parts else None

def _q(uri: str, local: str) -> str:
    return f'{{{uri}}}{local}'

def _split_tag(tag: str) -> tuple[str, str]:
    uri, _, local = tag[1:].partition('}')
    return (uri, local)

def _flatten(value: Any):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    else:
        yield value

def _element_value(el: ET.Element) -> Any:
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        return (el.text or '').strip()
    if len(children) == 1 and _split_tag(children[0].tag)[1] in {'Seq', 'Alt', 'Bag'}:
        items: list[Any] = []
        for li in children[0]:
            if not isinstance(li.tag, str) or _split_tag(li.tag)[1] != 'li':
                continue
            items.append(_li_value(li))
        return items
    return _struct_value(el)

def _li_value(li: ET.Element) -> Any:
    children = [c for c in li if isinstance(c.tag, str)]
    if not children:
        return (li.text or '').strip()
    return _struct_value(li)

def _struct_value(el: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in el.attrib.items():
        if not key.startswith('{'):
            continue
        uri, local = _split_tag(key)
        if uri == RDF:
            continue
        out[local] = value
    for child in el:
        if not isinstance(child.tag, str) or not child.tag.startswith('{'):
            continue
        uri, local = _split_tag(child.tag)
        if uri == RDF:
            continue
        out[local] = _element_value(child)
    return out
