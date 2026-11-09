"""Pedido de Registro de Evento (pedRegEvento) builders.

Substitution is not an event the taxpayer sends: issue a new DPS with ``Substitution``
and SEFIN generates event 105102 itself (posting 105102 directly is rejected, E0845).
"""

from __future__ import annotations

from datetime import datetime

from lxml import etree

from .documents import event_request_id, is_valid_cnpj, is_valid_cpf
from .dps import NFSE_NS, VER_APLIC, format_datetime, now_brasilia
from .environment import Environment

EVENT_VERSION = "1.01"
CANCELLATION = "101101"

CANCEL_REASON_ISSUE_ERROR = "1"
CANCEL_REASON_SERVICE_NOT_PROVIDED = "2"
CANCEL_REASON_OTHER = "9"


def _sub(parent: etree._Element, tag: str, text: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NFSE_NS}}}{tag}")
    el.text = text
    return el


def build_cancellation(
    access_key: str,
    reason_code: str,
    reason: str,
    environment: Environment,
    *,
    author_cnpj: str | None = None,
    author_cpf: str | None = None,
    dh_evento: datetime | None = None,
    ver_aplic: str = VER_APLIC,
) -> etree._Element:
    """Build an unsigned e101101 (Cancelamento de NFS-e) request.

    The author must be the holder of the signing certificate (E0812/E0815). ``reason`` is
    15-255 characters (TSMotivo).
    """
    if (author_cnpj is None) == (author_cpf is None):
        raise ValueError("pass exactly one of author_cnpj or author_cpf")
    if author_cnpj is not None and not is_valid_cnpj(author_cnpj):
        raise ValueError(f"invalid author CNPJ: {author_cnpj!r}")
    if author_cpf is not None and not is_valid_cpf(author_cpf):
        raise ValueError(f"invalid author CPF: {author_cpf!r}")
    if reason_code not in (CANCEL_REASON_ISSUE_ERROR, CANCEL_REASON_SERVICE_NOT_PROVIDED, CANCEL_REASON_OTHER):
        raise ValueError(f"cancellation cMotivo must be 1, 2 or 9: {reason_code!r}")
    if not 15 <= len(reason) <= 255:
        raise ValueError("cancellation xMotivo must be 15-255 characters")

    root = etree.Element(f"{{{NFSE_NS}}}pedRegEvento", nsmap={None: NFSE_NS}, versao=EVENT_VERSION)
    inf = etree.SubElement(root, f"{{{NFSE_NS}}}infPedReg", Id=event_request_id(access_key, CANCELLATION))
    _sub(inf, "tpAmb", environment.tp_amb)
    _sub(inf, "verAplic", ver_aplic)
    _sub(inf, "dhEvento", format_datetime(dh_evento or now_brasilia()))
    if author_cnpj is not None:
        _sub(inf, "CNPJAutor", author_cnpj)
    else:
        _sub(inf, "CPFAutor", author_cpf)
    _sub(inf, "chNFSe", access_key)
    e = etree.SubElement(inf, f"{{{NFSE_NS}}}e{CANCELLATION}")
    _sub(e, "xDesc", "Cancelamento de NFS-e")
    _sub(e, "cMotivo", reason_code)
    _sub(e, "xMotivo", reason)
    return root
