"""Throwaway test PKI: a CA, a server cert for localhost and an A1-like client PFX."""

from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

PFX_PASSWORD = b"test-only"


@dataclass(frozen=True)
class TestPki:
    ca_pem: bytes
    server_cert_pem: bytes
    server_key_pem: bytes
    client_pfx: bytes


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _issue(subject: str, key, issuer_name, issuer_key, *, ca=False, san=None) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False, key_agreement=False,
                          encipher_only=False, decipher_only=False),
            critical=True,
        )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def make_pki() -> TestPki:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = _issue("Test CA", ca_key, _name("Test CA"), ca_key, ca=True)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = _issue(
        "localhost", server_key, ca.subject, ca_key,
        san=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
    )
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _issue("EMPRESA TESTE LTDA:11222333000181", client_key, ca.subject, ca_key)
    pem = serialization.Encoding.PEM
    return TestPki(
        ca_pem=ca.public_bytes(pem),
        server_cert_pem=server.public_bytes(pem),
        server_key_pem=server_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        client_pfx=pkcs12.serialize_key_and_certificates(
            b"client", client_key, client, [ca], serialization.BestAvailableEncryption(PFX_PASSWORD)
        ),
    )
