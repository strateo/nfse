"""Enveloped XMLDSig for DPS and pedRegEvento, plus the wire serialization SEFIN expects.

The manuals do not pin algorithms. RSA-SHA1 + C14N 1.0 is what the government's own NFS-e
documents use and what nfse-php, PyNFSeNacional and Unimake send in production; RSA-SHA256
is accepted too (open-nfse). The signature must be an unprefixed ``<Signature>`` (E1228)
appended to the root, after ``infDPS``/``infPedReg``.
"""

from __future__ import annotations

import base64
import binascii
import enum
import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from lxml import etree

from .certificate import Certificate

DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
XML_PROLOG = b'<?xml version="1.0" encoding="UTF-8"?>'


class SignatureProfile(enum.Enum):
    RSA_SHA1 = ("http://www.w3.org/2000/09/xmldsig#rsa-sha1", "http://www.w3.org/2000/09/xmldsig#sha1", "sha1")
    RSA_SHA256 = ("http://www.w3.org/2001/04/xmldsig-more#rsa-sha256", "http://www.w3.org/2001/04/xmlenc#sha256", "sha256")

    @property
    def signature_uri(self) -> str:
        return self.value[0]

    @property
    def digest_uri(self) -> str:
        return self.value[1]

    @property
    def hash_name(self) -> str:
        return self.value[2]

    def hash_algorithm(self) -> hashes.HashAlgorithm:
        return hashes.SHA1() if self is SignatureProfile.RSA_SHA1 else hashes.SHA256()


def _ds(parent: etree._Element | None, tag: str, **attrib: str) -> etree._Element:
    name = f"{{{DSIG_NS}}}{tag}"
    if parent is None:
        return etree.Element(name, nsmap={None: DSIG_NS}, **attrib)
    return etree.SubElement(parent, name, **attrib)


def _c14n(element: etree._Element) -> bytes:
    """C14N 1.0 of a subtree, computed with the exclusive algorithm.

    lxml's inclusive C14N of a non-root element emits spurious ``xmlns=""`` on nested
    children, so its digest matches no other implementation. For documents with a single
    default namespace per subtree and no prefixes (enforced by ``_check_namespaces``) the
    inclusive and exclusive outputs are identical by definition; this reproduces the
    digest of PyNFSeNacional's PHP reference and verifies its signature.
    """
    return etree.tostring(element, method="c14n", exclusive=True, with_comments=False)


def _check_namespaces(root: etree._Element) -> None:
    for el in root.iter():
        if any(prefix is not None for prefix in el.nsmap):
            raise ValueError(f"prefixed namespace on <{etree.QName(el).localname}>: SEFIN rejects it (E1228)")


def _strip_whitespace(root: etree._Element) -> None:
    # Pretty-printing whitespace would be signed and then trip SEFIN's validation.
    for el in root.iter():
        if len(el) and el.text is not None and not el.text.strip():
            el.text = None
        if el.tail is not None and not el.tail.strip():
            el.tail = None


def sign(root: etree._Element, certificate: Certificate, profile: SignatureProfile = SignatureProfile.RSA_SHA1) -> etree._Element:
    """Sign the first child (``infDPS`` or ``infPedReg``) of ``root`` in place and return root."""
    if root.find(f"{{{DSIG_NS}}}Signature") is not None:
        raise ValueError("document is already signed")
    target = root[0]
    ref_id = target.get("Id")
    if not ref_id:
        raise ValueError(f"<{etree.QName(target).localname}> has no Id attribute to reference")
    _check_namespaces(root)
    _strip_whitespace(root)

    digest = base64.b64encode(hashlib.new(profile.hash_name, _c14n(target)).digest()).decode()

    signature = _ds(None, "Signature")
    signed_info = _ds(signature, "SignedInfo")
    _ds(signed_info, "CanonicalizationMethod", Algorithm=C14N)
    _ds(signed_info, "SignatureMethod", Algorithm=profile.signature_uri)
    reference = _ds(signed_info, "Reference", URI=f"#{ref_id}")
    transforms = _ds(reference, "Transforms")
    _ds(transforms, "Transform", Algorithm=ENVELOPED)
    _ds(transforms, "Transform", Algorithm=C14N)
    _ds(reference, "DigestMethod", Algorithm=profile.digest_uri)
    _ds(reference, "DigestValue").text = digest
    root.append(signature)

    # SignedInfo is canonicalized in its final position so inherited namespaces match
    # what a verifier sees.
    raw_signature = certificate.key.sign(_c14n(signed_info), padding.PKCS1v15(), profile.hash_algorithm())
    _ds(signature, "SignatureValue").text = base64.b64encode(raw_signature).decode()
    x509_data = _ds(_ds(signature, "KeyInfo"), "X509Data")
    der = certificate.cert.public_bytes(serialization.Encoding.DER)
    _ds(x509_data, "X509Certificate").text = base64.b64encode(der).decode()
    return root


def verify(root: etree._Element) -> None:
    """Check digest and RSA signature against the embedded certificate; raise ValueError if bad.

    A local self-check before blaming SEFIN for E0714; it does not validate the ICP-Brasil chain.
    """
    ns = {"ds": DSIG_NS}
    fiscal_ns = "http://www.sped.fazenda.gov.br/nfse"
    payloads = {"DPS": "infDPS", "NFSe": "infNFSe", "pedRegEvento": "infPedReg", "evento": "infEvento"}
    name = etree.QName(root)
    payload = payloads.get(name.localname)
    if name.namespace != fiscal_ns or payload is None:
        raise ValueError("unsupported fiscal document root")
    targets = root.findall(f"{{{fiscal_ns}}}{payload}")
    signatures = root.findall("ds:Signature", ns)
    if len(targets) != 1 or len(signatures) != 1 or root[0] is not targets[0]:
        raise ValueError("document needs one primary fiscal payload and one Signature")
    signature = signatures[0]
    signed_info = signature.find("ds:SignedInfo", ns)
    if signed_info is None:
        raise ValueError("missing SignedInfo")
    canonical = signed_info.find("ds:CanonicalizationMethod", ns)
    if canonical is None or canonical.get("Algorithm") != C14N:
        raise ValueError("unsupported canonicalization method")
    methods = signed_info.findall("ds:SignatureMethod", ns)
    if len(methods) != 1:
        raise ValueError("expected one SignatureMethod")
    method = methods[0].get("Algorithm")
    profile = next((p for p in SignatureProfile if p.signature_uri == method), None)
    if profile is None:
        raise ValueError(f"unsupported SignatureMethod {method}")
    references = signed_info.findall("ds:Reference", ns)
    if len(references) != 1:
        raise ValueError("expected one fiscal Reference")
    reference = references[0]
    target = targets[0]
    ref_id = target.get("Id")
    if not ref_id or reference.get("URI") != "#" + ref_id:
        raise ValueError("Reference must identify the primary fiscal payload")
    if sum(el.get("Id") == ref_id for el in root.iter()) != 1:
        raise ValueError("ambiguous duplicate fiscal Id")
    transforms = reference.findall("ds:Transforms/ds:Transform", ns)
    digest_method = reference.find("ds:DigestMethod", ns)
    if ([el.get("Algorithm") for el in transforms] != [ENVELOPED, C14N]
            or digest_method is None or digest_method.get("Algorithm") != profile.digest_uri):
        raise ValueError("unsupported digest or transform algorithms")
    expected = base64.b64encode(hashlib.new(profile.hash_name, _c14n(target)).digest()).decode()
    if reference.findtext("ds:DigestValue", namespaces=ns) != expected:
        raise ValueError("digest mismatch: signed content was altered")
    cert_text = signature.findtext("ds:KeyInfo/ds:X509Data/ds:X509Certificate", namespaces=ns)
    value_text = signature.findtext("ds:SignatureValue", namespaces=ns)
    if not cert_text or not value_text:
        raise ValueError("missing signature value or certificate")
    try:
        cert = x509.load_der_x509_certificate(base64.b64decode("".join(cert_text.split()), validate=True))
        value = base64.b64decode("".join(value_text.split()), validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("invalid signature value or certificate encoding") from e
    public_key = cert.public_key()
    if not isinstance(public_key, RSAPublicKey):
        raise ValueError("signature certificate must contain an RSA public key")
    try:
        public_key.verify(
            value,
            _c14n(signed_info),
            padding.PKCS1v15(),
            profile.hash_algorithm(),
        )
    except InvalidSignature as e:
        raise ValueError("signature value does not match SignedInfo") from e


def to_bytes(root: etree._Element) -> bytes:
    """Serialize for transmission: double-quoted UTF-8 prolog (E1229), no whitespace added."""
    return XML_PROLOG + etree.tostring(root, encoding="UTF-8", xml_declaration=False)
