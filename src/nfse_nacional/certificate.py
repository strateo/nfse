"""ICP-Brasil A1 certificate (.pfx/.p12): signing key and mTLS context."""

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
