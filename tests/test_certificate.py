import datetime
import pickle
import unittest

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from _certs import CLIENT_CNPJ, PFX_PASSWORD, ecnpj_certificate, ecnpj_other_name, make_pki
from nfse_nacional.certificate import Certificate, CertificateError
from nfse_nacional.errors import NfseError
ALPHANUMERIC = "12ABC34501DE35"


def _cert(**kwargs) -> Certificate:
    key, cert = ecnpj_certificate(**kwargs)
    return Certificate(key=key, cert=cert)


class CnpjTest(unittest.TestCase):
    def test_reads_the_ecnpj_field_from_a_pfx(self):
        cert = Certificate.from_pfx(make_pki().client_pfx, PFX_PASSWORD)
        self.assertEqual(cert.cnpj, CLIENT_CNPJ)
        cert.check(cnpj=CLIENT_CNPJ)

    def test_the_field_wins_over_the_display_name(self):
        self.assertEqual(_cert(subject="EMPRESA TESTE LTDA").cnpj, CLIENT_CNPJ)
        self.assertIsNone(_cert(san=()).cnpj, "a CN suffix alone is not an e-CNPJ")

    def test_accepts_string_types_used_by_icp_brasil_cas(self):
        for tag in (0x04, 0x0C, 0x13, 0x16):
            with self.subTest(tag=tag):
                self.assertEqual(_cert(san=(ecnpj_other_name(CLIENT_CNPJ, tag),)).cnpj, CLIENT_CNPJ)

    def test_alphanumeric_cnpj(self):
        cert = _cert(subject=f"EMPRESA:{ALPHANUMERIC}", san=(ALPHANUMERIC.lower(),))
        self.assertEqual(cert.cnpj, ALPHANUMERIC)
        cert.check(cnpj=ALPHANUMERIC)

    def test_ignores_invalid_or_malformed_values(self):
        for value in (ecnpj_other_name("11222333000182"), ecnpj_other_name(CLIENT_CNPJ, 0x02),
                      x509.OtherName(ecnpj_other_name(CLIENT_CNPJ).type_id, b"\x30\x10\x04\x0e" + CLIENT_CNPJ.encode())):
            with self.subTest(value=value.value):
                self.assertIsNone(_cert(subject="EMPRESA", san=(value,)).cnpj)

    def test_a_bad_entry_does_not_hide_a_good_one(self):
        self.assertEqual(_cert(san=(ecnpj_other_name("11222333000182"), CLIENT_CNPJ)).cnpj, CLIENT_CNPJ)

    def test_ignores_other_icp_brasil_fields(self):
        other = x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.4"), b"\x04\x0e" + CLIENT_CNPJ.encode())
        self.assertIsNone(_cert(san=(other,)).cnpj)


class CheckTest(unittest.TestCase):
    def assertCode(self, code, cert, **kwargs):
        with self.assertRaises(CertificateError) as caught:
            cert.check(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertIsInstance(caught.exception, NfseError)
        self.assertIsInstance(caught.exception, ValueError)

    def test_a_usable_certificate_passes(self):
        _cert().check()
        _cert(eku=()).check(cnpj=CLIENT_CNPJ)
        _cert(digital_signature=None).check()  # no KeyUsage extension at all

    def test_rejects_an_invalid_cnpj_argument_as_caller_error(self):
        for value in ("11.222.333/0001-81", " 11222333000181", "", "bogus"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                _cert().check(cnpj=value)
            self.assertNotIsInstance(caught.exception, CertificateError)

    def test_error_survives_pickling(self):
        error = pickle.loads(pickle.dumps(CertificateError("expired", "expired on 2026-01-01")))
        self.assertEqual((error.code, str(error)), ("expired", "expired on 2026-01-01"))

    def test_key_mismatch(self):
        _, cert = ecnpj_certificate()
        self.assertCode("key-mismatch", Certificate(key=rsa.generate_private_key(public_exponent=65537, key_size=2048), cert=cert))

    def test_weak_key(self):
        self.assertCode("weak-key", _cert(key=rsa.generate_private_key(public_exponent=65537, key_size=1024)))

    def test_cnpj_binding(self):
        self.assertCode("cnpj-inconsistent", _cert(subject="EMPRESA:11444777000161"))
        _cert(subject="11444777000161").check(cnpj=CLIENT_CNPJ)  # a CN without "NAME:" carries no CNPJ
        self.assertCode("not-ecnpj", _cert(san=()), cnpj=CLIENT_CNPJ)
        self.assertCode("cnpj-mismatch", _cert(), cnpj="11444777000161")
        _cert(san=()).check()  # without a required CNPJ, a non-e-CNPJ A1 can still be used

    def test_validity_window(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.assertCode("expired", _cert(), now=now + datetime.timedelta(days=2))
        self.assertCode("not-yet-valid", _cert(not_before=now + datetime.timedelta(days=1), not_after=now + datetime.timedelta(days=2)))
        self.assertCode("expired", _cert(not_before=now - datetime.timedelta(days=2), not_after=now - datetime.timedelta(days=1)))
        _cert().check(now=now)

    def test_key_usage(self):
        self.assertCode("key-usage", _cert(digital_signature=False))
        self.assertCode("client-auth", _cert(eku=(ExtendedKeyUsageOID.EMAIL_PROTECTION,)))

    def test_reports_problems_in_fix_order(self):
        expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        self.assertCode("cnpj-mismatch", _cert(not_before=expired - datetime.timedelta(days=1), not_after=expired), cnpj="11444777000161")


if __name__ == "__main__":
    unittest.main()
