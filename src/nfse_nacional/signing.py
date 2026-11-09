"""Enveloped XMLDSig for DPS and pedRegEvento, plus the wire serialization SEFIN expects.

The manuals do not pin algorithms. RSA-SHA1 + C14N 1.0 is what the government's own NFS-e
documents use and what nfse-php, PyNFSeNacional and Unimake send in production; RSA-SHA256
is accepted too (open-nfse). The signature must be an unprefixed ``<Signature>`` (E1228)
appended to the root, after ``infDPS``/``infPedReg``.
"""

from __future__ import annotations

import base64
import enum
import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
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
    signature = root.find("ds:Signature", ns)
    if signature is None:
        raise ValueError("no Signature element")
    signed_info = signature.find("ds:SignedInfo", ns)
    method = signed_info.find("ds:SignatureMethod", ns).get("Algorithm")
    profile = next((p for p in SignatureProfile if p.signature_uri == method), None)
    if profile is None:
        raise ValueError(f"unsupported SignatureMethod {method}")
    reference = signed_info.find("ds:Reference", ns)
    ref_id = reference.get("URI", "").removeprefix("#")
    target = next((el for el in root.iter() if el.get("Id") == ref_id), None)
    if target is None:
        raise ValueError("referenced element not found")
    expected = base64.b64encode(hashlib.new(profile.hash_name, _c14n(target)).digest()).decode()
    if reference.findtext("ds:DigestValue", namespaces=ns) != expected:
        raise ValueError("digest mismatch: signed content was altered")
    cert = x509.load_der_x509_certificate(base64.b64decode(signature.findtext(".//ds:X509Certificate", namespaces=ns)))
    try:
        cert.public_key().verify(
            base64.b64decode(signature.findtext("ds:SignatureValue", namespaces=ns)),
            _c14n(signed_info),
            padding.PKCS1v15(),
            profile.hash_algorithm(),
        )
    except InvalidSignature as e:
        raise ValueError("signature value does not match SignedInfo") from e


def to_bytes(root: etree._Element) -> bytes:
    """Serialize for transmission: double-quoted UTF-8 prolog (E1229), no whitespace added."""
    return XML_PROLOG + etree.tostring(root, encoding="UTF-8", xml_declaration=False)
