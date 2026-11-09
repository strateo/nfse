"""CPF/CNPJ validation and the identifiers derived from them (DPS Id, event Id)."""

from __future__ import annotations

import re

_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_CPF_RE = re.compile(r"^[0-9]{11}$")
_CNPJ_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def _mod11_dv(values: list[int], weights: tuple[int, ...]) -> int:
    rest = sum(v * w for v, w in zip(values, weights)) % 11
    return 0 if rest < 2 else 11 - rest


def is_valid_cnpj(cnpj: str) -> bool:
    """Validate a numeric or alphanumeric CNPJ (Receita Federal, in force since July 2026).

    Each character is worth ``ord(c) - 48`` (so '0'..'9' -> 0..9, 'A' -> 17 ... 'Z' -> 42);
    the two check digits stay numeric and use the classic mod-11 weights.
    """
    if not _CNPJ_RE.match(cnpj) or len(set(cnpj)) == 1:
        return False
    values = [ord(c) - 48 for c in cnpj]
    dv1 = _mod11_dv(values[:12], _CNPJ_WEIGHTS[1:])
    dv2 = _mod11_dv(values[:12] + [dv1], _CNPJ_WEIGHTS)
    return values[12] == dv1 and values[13] == dv2


def is_valid_cpf(cpf: str) -> bool:
    if not _CPF_RE.match(cpf) or len(set(cpf)) == 1:
        return False
    digits = [int(c) for c in cpf]
    for n in (9, 10):
        total = sum(d * w for d, w in zip(digits[:n], range(n + 1, 1, -1)))
        dv = (total * 10) % 11 % 10
        if digits[n] != dv:
            return False
    return True


def dps_id(c_loc_emi: str, *, cnpj: str | None = None, cpf: str | None = None, serie: str, n_dps: str) -> str:
    """Build the 45-char ``infDPS/@Id``: "DPS" + cLocEmi(7) + tpInsc(1) + inscription(14) + serie(5) + nDPS(15).

    tpInsc is 2 for CNPJ and 1 for CPF (CPF left-padded to 14). Pass ``serie``/``n_dps``
    exactly as written in the XML: '1' and '00001' produce different Ids (E0004).
    """
    if (cnpj is None) == (cpf is None):
        raise ValueError("pass exactly one of cnpj or cpf")
    if not re.fullmatch(r"[0-9]{7}", c_loc_emi):
        raise ValueError(f"cLocEmi must be a 7-digit IBGE code: {c_loc_emi!r}")
    if not re.fullmatch(r"[0-9]{1,4}|[0-8][0-9]{4}", serie):
        raise ValueError(f"serie must be 1-5 digits below 90000: {serie!r}")
    if not re.fullmatch(r"[1-9][0-9]{0,14}", n_dps):
        raise ValueError(f"nDPS must be 1-15 digits without leading zeros: {n_dps!r}")
    if cnpj is not None:
        if not is_valid_cnpj(cnpj):
            raise ValueError(f"invalid CNPJ: {cnpj!r}")
        inscription = "2" + cnpj
    else:
        if not is_valid_cpf(cpf):
            raise ValueError(f"invalid CPF: {cpf!r}")
        inscription = "1" + cpf.zfill(14)
    return f"DPS{c_loc_emi}{inscription}{serie.zfill(5)}{n_dps.zfill(15)}"


def event_request_id(access_key: str, event_code: str) -> str:
    """Build the 59-char ``infPedReg/@Id``: "PRE" + access key(50) + event type(6).

    Anexo II v1.01 dropped ``nPedRegEvento`` from the Id; the old 62-char form fails with E1235.
    """
    if not re.fullmatch(r"[0-9]{8}(1[0-9]{14}|2[0-9A-Z]{14})[0-9]{27}", access_key):
        raise ValueError(f"malformed NFS-e access key: {access_key!r}")
    if not re.fullmatch(r"[0-9]{6}", event_code):
        raise ValueError(f"event code must be 6 digits: {event_code!r}")
    return f"PRE{access_key}{event_code}"
