"""Exceptions raised by the client and parsing of SEFIN/ADN error bodies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Rejection codes that mean "try again later", not "fix the document".
TRANSIENT_CODES = frozenset({"E1217", "E1206"})  # service paused for maintenance; LCR access failure


@dataclass(frozen=True)
class Message:
    code: str
    description: str
    complement: str | None = None


class NfseError(Exception):
    """Base class for every error this package raises."""


class TransportError(NfseError):
    """Network failure, timeout, or an HTTP answer without a usable SEFIN/ADN body.

    ``retry_after`` is the server's Retry-After in seconds when it sent one (429/503).
    """

    def __init__(self, message: str, *, status: int | None = None, body: bytes = b"", retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


class CertificateError(NfseError, ValueError):
    """The A1 certificate cannot sign or authenticate an NFS-e request.

    ``code`` is stable: key-mismatch, weak-key, cnpj-inconsistent, not-ecnpj, cnpj-mismatch,
    not-yet-valid, expired, key-usage, client-auth.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def __reduce__(self):
        return type(self), (self.code, str(self))


class RejectionError(NfseError):
    """SEFIN/ADN processed the request and refused it; ``messages`` holds the E-codes."""

    def __init__(self, status: int, messages: list[Message], body: dict[str, Any]):
        summary = "; ".join(f"[{m.code}] {m.description}" for m in messages) or "no error messages in body"
        super().__init__(f"HTTP {status}: {summary}")
        self.status = status
        self.messages = messages
        self.body = body

    @property
    def codes(self) -> list[str]:
        return [m.code for m in self.messages]

    @property
    def transient(self) -> bool:
        return any(code in TRANSIENT_CODES for code in self.codes)


def _get(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def parse_messages(body: dict[str, Any], *keys: str) -> list[Message]:
    """Read error/alert messages tolerating the casings and shapes seen in the wild.

    SEFIN uses camelCase (``erros``/``codigo``), ADN PascalCase (``Erros``/``Codigo``), and the
    events API documents ``erro`` as an object while production returns a list.
    """
    raw = _get(body, *keys)
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    messages = []
    for item in items:
        if not isinstance(item, dict):
            messages.append(Message(code="UNKNOWN", description=str(item)))
            continue
        messages.append(
            Message(
                code=str(_get(item, "codigo", "Codigo") or "UNKNOWN"),
                description=str(_get(item, "descricao", "Descricao") or ""),
                complement=_get(item, "complemento", "Complemento"),
            )
        )
    return messages
