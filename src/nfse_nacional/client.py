"""HTTP clients for SEFIN Nacional (issuance, events) and ADN (DF-e distribution).
Payloads are signed XML documents, gzip-compressed and base64-encoded inside a JSON
envelope. Business rejections come back as JSON with E-codes and raise RejectionError;
anything without a usable body raises TransportError.
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import gzip
import http.client
import io
import json
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .environment import Environment
from .errors import Message, RejectionError, TransportError, parse_messages

DEFAULT_TIMEOUT = 60.0
MAX_XML_BYTES = 50 * 1024 * 1024


def encode_xml(xml: bytes) -> str:
    return base64.b64encode(gzip.compress(xml)).decode("ascii")


def decode_xml(data: str) -> bytes:
    try:
        raw = base64.b64decode(data, validate=True)
        if not raw.startswith(b"\x1f\x8b"):
            # Some event responses are encoded twice.
            raw = base64.b64decode(raw, validate=True)
        if not raw.startswith(b"\x1f\x8b"):
            raise ValueError("expected gzip data")
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
            xml = f.read(MAX_XML_BYTES + 1)
    except (binascii.Error, ValueError, TypeError, OSError, EOFError, zlib.error) as e:
        raise TransportError("invalid compressed XML response") from e
    if not xml:
        raise TransportError("empty XML response")
    if len(xml) > MAX_XML_BYTES:
        raise TransportError("decompressed XML exceeds 50 MB")
    return xml


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.body)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


class _Http:
    def __init__(self, base_url: str, context: ssl.SSLContext, timeout: float):
        self._base = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
        self._timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Response:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._base + path, data=data, method=method, headers=headers)
        try:
            try:
                resp = self._opener.open(req, timeout=self._timeout)
            except urllib.error.HTTPError as error_response:
                resp = error_response
            with resp:
                return Response(resp.status, resp.read(), dict(resp.headers or {}))
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            raise TransportError(f"{method} request failed") from e


def _transport_error(resp: Response, what: str) -> TransportError:
    return TransportError(
        f"{what}: unexpected HTTP {resp.status}",
        status=resp.status,
        body=resp.body[:2000],
        retry_after=_retry_after(next((v for k, v in resp.headers.items() if k.lower() == "retry-after"), None)),
    )


@dataclass(frozen=True)
class EmissionResult:
    access_key: str
    nfse_xml: bytes
    dps_id: str | None
    alerts: list[Message]
    raw: dict[str, Any]


@dataclass(frozen=True)
class DfeDocument:
    nsu: int
    access_key: str | None
    document_type: str | None
    event_type: str | None
    generated_at: str | None
    xml: bytes | None


@dataclass(frozen=True)
class DfeBatch:
    status: str
    documents: list[DfeDocument]
    messages: list[Message]
    raw: dict[str, Any]

    @property
    def last_nsu(self) -> int | None:
        return max((d.nsu for d in self.documents), default=None)

    @property
    def exhausted(self) -> bool:
        return self.status.replace("_", "").upper() == "NENHUMDOCUMENTOLOCALIZADO"


class SefinClient:
    """SEFIN Nacional: issue NFS-e from a signed DPS, query it, register events."""

    def __init__(self, context: ssl.SSLContext, environment: Environment, timeout: float = DEFAULT_TIMEOUT):
        self.environment = environment
        self._http = _Http(environment.sefin_url, context, timeout)

    def emit(self, signed_dps: bytes) -> EmissionResult:
        """POST /nfse. Synchronous: returns the NFS-e or raises RejectionError.

        Persist ``signed_dps`` before calling. On TransportError do not build a new DPS;
        call :meth:`recover` with the same document so the nDPS is not burned twice.
        """
        resp = self._http.request("POST", "/nfse", {"dpsXmlGZipB64": encode_xml(signed_dps)})
        body = resp.json()
        if resp.status in (200, 201) and body and isinstance(body.get("chaveAcesso"), str) and isinstance(body.get("nfseXmlGZipB64"), str):
            return EmissionResult(
                access_key=body["chaveAcesso"],
                nfse_xml=decode_xml(body["nfseXmlGZipB64"]),
                dps_id=body.get("idDps") or body.get("idDPS"),
                alerts=parse_messages(body, "alertas", "Alertas"),
                raw=body,
            )
        if resp.status in (400, 422) and body is not None:
            raise RejectionError(resp.status, parse_messages(body, "erros", "Erros", "erro"), body)
        raise _transport_error(resp, "emit")

    def recover(self, signed_dps: bytes, dps_id: str) -> EmissionResult:
        """Finish an emission whose outcome is unknown (timeout, 5xx, crash after send).

        Returns the NFS-e SEFIN already generated for ``dps_id``, or re-sends the same signed
        document. Never rebuild the DPS here: a new nDPS would issue a second note.
        """
        key = self.get_access_key(dps_id)
        if key is None:
            return self.emit(signed_dps)
        return EmissionResult(access_key=key, nfse_xml=self.get_nfse(key), dps_id=dps_id, alerts=[], raw={})

    def get_nfse(self, access_key: str) -> bytes:
        resp = self._http.request("GET", f"/nfse/{_seg(access_key)}")
        body = resp.json()
        if resp.status == 200 and body and isinstance(body.get("nfseXmlGZipB64"), str):
            return decode_xml(body["nfseXmlGZipB64"])
        if resp.status in (400, 404) and body is not None:
            raise RejectionError(resp.status, parse_messages(body, "erros", "Erros", "erro"), body)
        raise _transport_error(resp, "get_nfse")

    def dps_exists(self, dps_id: str) -> bool:
        """HEAD /dps/{id}: whether an NFS-e was generated from this DPS (any valid certificate)."""
        resp = self._http.request("HEAD", f"/dps/{_seg(dps_id)}")
        if resp.status == 200:
            return True
        if resp.status == 404:
            return False
        raise _transport_error(resp, "dps_exists")

    def get_access_key(self, dps_id: str) -> str | None:
        """GET /dps/{id}: the access key of the NFS-e generated from this DPS, or None.

        Only answers a certificate that is a party (prestador, tomador, intermediário) of the note.
        """
        resp = self._http.request("GET", f"/dps/{_seg(dps_id)}")
        body = resp.json()
        if resp.status == 200 and body and isinstance(body.get("chaveAcesso"), str):
            return body["chaveAcesso"]
        if resp.status == 404 and body is not None:
            return None
        if resp.status == 400 and body is not None:
            raise RejectionError(resp.status, parse_messages(body, "erros", "Erros", "erro"), body)
        raise _transport_error(resp, "get_access_key")

    def register_event(self, access_key: str, signed_event: bytes) -> bytes:
        """POST /nfse/{key}/eventos with a signed pedRegEvento; returns the event XML.

        Duplicate rejections such as E0840 require querying and matching the actual
        authoritative event. A rejection alone never confirms this request succeeded.
        """
        payload = {"pedidoRegistroEventoXmlGZipB64": encode_xml(signed_event)}
        resp = self._http.request("POST", f"/nfse/{_seg(access_key)}/eventos", payload)
        body = resp.json()
        if resp.status in (200, 201) and body and isinstance(body.get("eventoXmlGZipB64"), str):
            return decode_xml(body["eventoXmlGZipB64"])
        if resp.status in (400, 422) and body is not None:
            raise RejectionError(resp.status, parse_messages(body, "erro", "erros", "Erros"), body)
        raise _transport_error(resp, "register_event")

    def get_event(self, access_key: str, event_code: str, sequence: int = 1) -> bytes:
        resp = self._http.request("GET", f"/nfse/{_seg(access_key)}/eventos/{_seg(event_code)}/{int(sequence)}")
        body = resp.json()
        if resp.status == 200 and body and isinstance(body.get("eventoXmlGZipB64"), str):
            return decode_xml(body["eventoXmlGZipB64"])
        if resp.status in (400, 404) and body is not None:
            raise RejectionError(resp.status, parse_messages(body, "erro", "erros", "Erros"), body)
        raise _transport_error(resp, "get_event")


class AdnClient:
    """ADN contribuintes: documents (NFS-e, events) where the certificate holder is a party."""

    def __init__(self, context: ssl.SSLContext, environment: Environment, timeout: float = DEFAULT_TIMEOUT):
        self.environment = environment
        self._http = _Http(environment.adn_url, context, timeout)

    def fetch_dfe(self, last_nsu: int, cnpj: str | None = None) -> DfeBatch:
        """GET /DFe/{NSU}: documents after ``last_nsu``. Persist ``batch.last_nsu`` as the next cursor.

        ``cnpj`` queries another establishment sharing the certificate's CNPJ root. An empty
        queue comes back as HTTP 404 with a normal body; that is ``batch.exhausted``, not an error.
        """
        query = {"lote": "true"}
        if cnpj is not None:
            query["cnpjConsulta"] = cnpj
        resp = self._http.request("GET", f"/DFe/{int(last_nsu)}?{urllib.parse.urlencode(query)}")
        body = resp.json()
        # A 4xx without StatusProcessamento is a proxy/WAF page, not "caught up".
        if body is None or not isinstance(body.get("StatusProcessamento"), str):
            raise _transport_error(resp, "fetch_dfe")
        status = body["StatusProcessamento"].replace("_", "").upper()
        messages = parse_messages(body, "Erros", "erros")
        if status == "REJEICAO" and resp.status in (400, 422):
            raise RejectionError(resp.status, messages, body)
        if not ((resp.status == 200 and status in ("DOCUMENTOSLOCALIZADOS", "NENHUMDOCUMENTOLOCALIZADO"))
                or (resp.status == 404 and status == "NENHUMDOCUMENTOLOCALIZADO")):
            raise _transport_error(resp, "fetch_dfe")
        items = body.get("LoteDFe", [])
        if not isinstance(items, list) or (status == "NENHUMDOCUMENTOLOCALIZADO" and items):
            raise _transport_error(resp, "fetch_dfe")
        if status == "DOCUMENTOSLOCALIZADOS" and not items:
            raise _transport_error(resp, "fetch_dfe: documents located without documents")
        for item in items:
            if not isinstance(item, dict):
                raise _transport_error(resp, "fetch_dfe")
            nsu = item.get("NSU")
            if (isinstance(nsu, bool) or not isinstance(nsu, (str, int))
                    or not str(nsu).isascii() or not str(nsu).isdigit()):
                raise _transport_error(resp, "fetch_dfe")
        if items and max(int(item["NSU"]) for item in items) <= int(last_nsu):
            raise _transport_error(resp, "fetch_dfe: batch does not advance NSU")
        documents = [
            DfeDocument(
                nsu=int(item["NSU"]),
                access_key=item.get("ChaveAcesso"),
                document_type=item.get("TipoDocumento"),
                event_type=item.get("TipoEvento"),
                generated_at=item.get("DataHoraGeracao"),
                xml=decode_xml(item["ArquivoXml"]) if item.get("ArquivoXml") else None,
            )
            for item in items
        ]
        return DfeBatch(status=body["StatusProcessamento"], documents=documents, messages=messages, raw=body)


def _seg(value: str) -> str:
    return urllib.parse.quote(value, safe="")
