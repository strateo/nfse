"""Throwaway test PKI: a CA, a server cert for localhost and an e-CNPJ-like A1 client PFX."""

from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

PFX_PASSWORD = b"test-only"
CLIENT_CNPJ = "11222333000181"


@dataclass(frozen=True)
class TestPki:
    ca_pem: bytes
    server_cert_pem: bytes
    server_key_pem: bytes
    client_pfx: bytes


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _issue(subject: str, key, issuer_name, issuer_key, *, ca=False, san=None, extensions=(),
           not_before=None, not_after=None) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now - datetime.timedelta(minutes=5))
        .not_valid_after(not_after or now + datetime.timedelta(days=1))
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
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical=critical)
    return builder.sign(issuer_key, hashes.SHA256())


def ecnpj_other_name(cnpj: str, tag: int = 0x04) -> x509.OtherName:
    """ICP-Brasil e-CNPJ field: the CNPJ as a short DER string (OCTET STRING by default)."""
    return x509.OtherName(ObjectIdentifier("2.16.76.1.3.3"), bytes([tag, len(cnpj)]) + cnpj.encode())


def _usage(*, digital_signature=True):
    return x509.KeyUsage(digital_signature=digital_signature, content_commitment=True, key_encipherment=True,
                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                         encipher_only=False, decipher_only=False)


def ecnpj_certificate(subject=f"EMPRESA TESTE LTDA:{CLIENT_CNPJ}", *, key=None, san=(CLIENT_CNPJ,), digital_signature=True,
                      eku=(ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.EMAIL_PROTECTION), not_before=None,
                      not_after=None) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Self-issued A1 shaped like an ICP-Brasil e-CNPJ; each argument breaks one property."""
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    extensions = [(_usage(digital_signature=digital_signature), True)]
    if eku:
        extensions.append((x509.ExtendedKeyUsage(list(eku)), False))
    names = [ecnpj_other_name(value) if isinstance(value, str) else value for value in san]
    cert = _issue(subject, key, _name("Test AC"), key, san=names or None, extensions=extensions,
                  not_before=not_before, not_after=not_after)
    return key, cert


def make_pki() -> TestPki:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = _issue("Test CA", ca_key, _name("Test CA"), ca_key, ca=True)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = _issue(
        "localhost", server_key, ca.subject, ca_key,
        san=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
    )
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _issue(f"EMPRESA TESTE LTDA:{CLIENT_CNPJ}", client_key, ca.subject, ca_key, san=[ecnpj_other_name(CLIENT_CNPJ)],
                    extensions=[(_usage(), True), (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), False)])
    pem = serialization.Encoding.PEM
    return TestPki(
        ca_pem=ca.public_bytes(pem),
        server_cert_pem=server.public_bytes(pem),
        server_key_pem=server_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        client_pfx=pkcs12.serialize_key_and_certificates(
            b"client", client_key, client, [ca], serialization.BestAvailableEncryption(PFX_PASSWORD)
        ),
    )
