"""Local XSD validation against the official schema bundles shipped in ``schemas/``.

``producao`` is the bundle published on "Documentação Atual" (2026-02-09). It predates the
alphanumeric-CNPJ rollout announced for 2026-08-10, so its TSCNPJ rejects letters. A narrow
runtime compatibility fix follows the newer official TSCNPJ pattern; it is not live issuer
proof. ``producao_restrita`` is the 2026-10-20 (NT 009) bundle.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from urllib.parse import urlparse
from urllib.request import url2pathname

from lxml import etree

from .environment import Layout

_BUNDLES = {Layout.V1_01: "producao", Layout.NT009: "producao_restrita"}
_ROOTS = {
    "DPS": "DPS_v1.01.xsd",
    "pedRegEvento": "pedRegEvento_v1.01.xsd",
    "evento": "evento_v1.01.xsd",
    "NFSe": "NFSe_v1.01.xsd",
}


class SchemaValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("XSD validation failed:\n" + "\n".join(errors))
        self.errors = errors


class _OfficialSchemaFixes(etree.Resolver):
    """Apply narrow compatibility fixes to stale official schema bundles at load time.

    The production bundle declares TSCNPJ as numeric, although alphanumeric CNPJ was
    scheduled for production on 2026-08-10. The 2026-02-09 TSSerieDPS pattern also has
    anchors unsupported by XSD regular expressions. Each fix matches the exact outdated
    declaration and stops applying automatically when the official bundle is refreshed.
    """

    _BROKEN = 'value="^0{0,4}\\d{1,5}$"'

    def resolve(self, url, id, context):
        if not url.endswith("tiposSimples_v1.01.xsd"):
            return None
        path = url2pathname(urlparse(url).path) if url.startswith("file:") else url
        with open(path, encoding="utf-8-sig") as f:
            original = f.read()
        text = original
        if self._BROKEN in text:
            text = text.replace(self._BROKEN, 'value="0{0,4}\\d{1,5}"')
        tree = etree.fromstring(text.encode("utf-8"))
        pattern = tree.find(
            "{http://www.w3.org/2001/XMLSchema}simpleType[@name='TSCNPJ']/"
            "{http://www.w3.org/2001/XMLSchema}restriction/"
            "{http://www.w3.org/2001/XMLSchema}pattern"
        )
        if pattern is not None and pattern.get("value") == "[0-9]{14}":
            pattern.set("value", "[0-9A-Z]{12}[0-9]{2}")
            text = etree.tostring(tree, encoding="unicode")
        if text == original:
            return None
        return self.resolve_string(text, context, base_url=url)


@cache
def _schema(layout: Layout, root: str) -> etree.XMLSchema:
    path = resources.files("nfse_nacional") / "schemas" / _BUNDLES[layout] / _ROOTS[root]
    with resources.as_file(path) as file:
        parser = etree.XMLParser(no_network=True, resolve_entities=True)
        parser.resolvers.add(_OfficialSchemaFixes())
        return etree.XMLSchema(etree.parse(str(file), parser))


def validate(element: etree._Element, layout: Layout) -> None:
    """Validate DPS, NFS-e, event requests or registered events against their layout XSD."""
    name = etree.QName(element).localname
    if name not in _ROOTS:
        raise ValueError(f"no schema for root element {name!r}")
    schema = _schema(layout, name)
    if not schema.validate(element):
        raise SchemaValidationError([f"line {e.line}: {e.message}" for e in schema.error_log])
