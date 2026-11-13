"""ICP-Brasil A1 certificate (.pfx/.p12): signing key, mTLS context and e-CNPJ checks."""

from __future__ import annotations

import os
import secrets
import shutil
import ssl
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .documents import is_valid_cnpj
from .errors import CertificateError

ECNPJ_OID = "2.16.76.1.3.3"  # DOC-ICP-04: the company's CNPJ in a SubjectAlternativeName otherName
MIN_KEY_BITS = 2048  # DOC-ICP-04 §6.1.5 and DOC-ICP-01.01: minimum RSA size for A1 keys


def _der_string(value: bytes) -> str | None:
    """Text of a short DER OCTET/UTF8/Printable/IA5 string; ICP-Brasil CAs use several."""
    if len(value) < 2 or value[0] not in (0x04, 0x0C, 0x13, 0x16) or value[1] >= 0x80 or len(value) != 2 + value[1]:
        return None
    try:
        return value[2:].decode("ascii").strip()
    except UnicodeDecodeError:
        return None


@dataclass(frozen=True)
class Certificate:
    key: RSAPrivateKey
    cert: x509.Certificate
    chain: tuple[x509.Certificate, ...] = ()

    @classmethod
    def from_pfx(cls, data: bytes, password: str | bytes | None) -> Certificate:
        if isinstance(password, str):
            password = password.encode()
        key, cert, chain = pkcs12.load_key_and_certificates(data, password)
        if cert is None or not isinstance(key, RSAPrivateKey):
            raise ValueError("PFX must contain an RSA private key and its certificate")
        return cls(key=key, cert=cert, chain=tuple(chain))

    @classmethod
    def from_pfx_file(cls, path: str | os.PathLike[str], password: str | bytes | None) -> Certificate:
        return cls.from_pfx(Path(path).read_bytes(), password)

    @property
    def not_valid_after(self) -> datetime:
        return self.cert.not_valid_after_utc

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) > self.not_valid_after

    @property
    def cnpj(self) -> str | None:
        """The e-CNPJ holder's CNPJ from the ICP-Brasil otherName, or None when absent or invalid.

        The subject CN ("NAME:CNPJ") is a display convention, not the authoritative field.
        """
        try:
            san = self.cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return None
        for name in san.get_values_for_type(x509.OtherName):
            if name.type_id.dotted_string == ECNPJ_OID:
                text = (_der_string(name.value) or "").upper()
                if is_valid_cnpj(text):
                    return text
        return None

    def check(self, *, cnpj: str | None = None, now: datetime | None = None) -> None:
        """Raise CertificateError when the certificate cannot be used for NFS-e, in fix order.

        ``cnpj`` (14 characters, unformatted) additionally requires an e-CNPJ issued to that
        company; an invalid ``cnpj`` raises a plain ValueError. ``now`` must be timezone-aware.
        The ICP-Brasil chain and revocation are not checked; SEFIN validates them on every request.
        """
        if cnpj is not None and not is_valid_cnpj(cnpj.upper()):
            raise ValueError("cnpj must be a valid 14-character CNPJ without punctuation")
        if self.key.public_key().public_numbers() != self.cert.public_key().public_numbers():
            raise CertificateError("key-mismatch", "the private key does not match the certificate")
        if self.key.key_size < MIN_KEY_BITS:
            raise CertificateError("weak-key", f"RSA key below {MIN_KEY_BITS} bits")
        holder = self.cnpj
        cn = self.cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        _, separator, suffix = str(cn[0].value).rpartition(":") if cn else ("", "", "")
        suffix = suffix.strip().upper()
        if holder and separator and is_valid_cnpj(suffix) and suffix != holder:
            raise CertificateError("cnpj-inconsistent", "the subject CN and the e-CNPJ field carry different CNPJs")
        if cnpj is not None:
            if holder is None:
                raise CertificateError("not-ecnpj", "not an ICP-Brasil e-CNPJ: no readable e-CNPJ field")
            if holder != cnpj.upper():
                raise CertificateError("cnpj-mismatch", f"certificate issued to CNPJ {holder}, not {cnpj}")
        now = now or datetime.now(timezone.utc)
        if now < self.cert.not_valid_before_utc:
            raise CertificateError("not-yet-valid", f"valid only from {self.cert.not_valid_before_utc:%Y-%m-%d}")
        if now > self.not_valid_after:
            raise CertificateError("expired", f"expired on {self.not_valid_after:%Y-%m-%d}")
        try:
            usage = self.cert.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            usage = None
        if usage is not None and not usage.digital_signature:
            raise CertificateError("key-usage", "the certificate does not allow digital signatures")
        try:
            extended = self.cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        except x509.ExtensionNotFound:
            extended = None
        if extended is not None and ExtendedKeyUsageOID.CLIENT_AUTH not in extended:
            raise CertificateError("client-auth", "the certificate does not allow TLS client authentication, which SEFIN requires")

    def ssl_context(self, cafile: str | None = None) -> ssl.SSLContext:
        """Client-authenticated TLS context, HTTP/1.1 only (SEFIN refuses HTTP/2).

        ``load_cert_chain`` only reads files, so the key is written to a private temp dir
        encrypted with a one-time passphrase and removed before returning.
        """
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.set_alpn_protocols(["http/1.1"])
        passphrase = secrets.token_urlsafe(32).encode()
        tmp = tempfile.mkdtemp(prefix="nfse-")
        try:
            cert_path = os.path.join(tmp, "cert.pem")
            key_path = os.path.join(tmp, "key.pem")
            with open(cert_path, "wb") as f:
                for c in (self.cert, *self.chain):
                    f.write(c.public_bytes(serialization.Encoding.PEM))
            with open(key_path, "wb") as f:
                f.write(
                    self.key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.BestAvailableEncryption(passphrase),
                    )
                )
            ctx.load_cert_chain(cert_path, key_path, password=passphrase)
        finally:
            shutil.rmtree(tmp)
        return ctx
