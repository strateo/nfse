import unittest
from pathlib import Path

from lxml import etree

from _certs import PFX_PASSWORD, make_pki
from nfse_nacional import events, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional.dps import build_dps
from nfse_nacional.environment import PRODUCAO, PRODUCAO_RESTRITA
from test_dps import ACCESS_KEY, me_epp_dps

FIXTURES = Path(__file__).parent / "fixtures"
DS = {"ds": signing.DSIG_NS}


class SigningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert = Certificate.from_pfx(make_pki().client_pfx, PFX_PASSWORD)

    def test_interoperates_with_independent_signer(self):
        # Signed by PyNFSeNacional's PHP reference: our C14N must reproduce its digest.
        signing.verify(etree.parse(str(FIXTURES / "pynfsenacional_golden_dps_signed.xml")).getroot())

    def test_signed_dps_verifies_and_stays_xsd_valid(self):
        for env in (PRODUCAO, PRODUCAO_RESTRITA):
            for profile in signing.SignatureProfile:
                with self.subTest(env=env.name, profile=profile.name):
                    root = signing.sign(build_dps(me_epp_dps(), env), self.cert, profile)
                    signing.verify(root)
                    schemas.validate(root, env.layout)
                    reparsed = etree.fromstring(signing.to_bytes(root))
                    signing.verify(reparsed)

    def test_signature_shape(self):
        root = signing.sign(build_dps(me_epp_dps(), PRODUCAO), self.cert)
        sig = root[-1]
        self.assertEqual(sig.tag, f"{{{signing.DSIG_NS}}}Signature")
        self.assertEqual(sig.nsmap, {None: signing.DSIG_NS})
        self.assertEqual(sig.find("ds:SignedInfo/ds:Reference", DS).get("URI"), "#" + root[0].get("Id"))
        self.assertEqual(
            [t.get("Algorithm") for t in sig.iterfind("ds:SignedInfo/ds:Reference/ds:Transforms/ds:Transform", DS)],
            [signing.ENVELOPED, signing.C14N],
        )
        self.assertEqual([etree.QName(e).localname for e in sig], ["SignedInfo", "SignatureValue", "KeyInfo"])

    def test_wire_format(self):
        data = signing.to_bytes(signing.sign(build_dps(me_epp_dps(), PRODUCAO), self.cert))
        self.assertTrue(data.startswith(b'<?xml version="1.0" encoding="UTF-8"?><DPS xmlns="http://www.sped.fazenda.gov.br/nfse"'))
        self.assertNotIn(b"\n", data)
        self.assertNotIn(b"ds:", data)

    def test_tampering_is_detected(self):
        root = signing.sign(build_dps(me_epp_dps(), PRODUCAO), self.cert)
        root.find(".//{http://www.sped.fazenda.gov.br/nfse}vServ").text = "1.00"
        with self.assertRaisesRegex(ValueError, "digest"):
            signing.verify(root)

    def test_refuses_double_signing_and_prefixes(self):
        root = signing.sign(build_dps(me_epp_dps(), PRODUCAO), self.cert)
        with self.assertRaises(ValueError):
            signing.sign(root, self.cert)
        prefixed = etree.fromstring(b'<n:DPS xmlns:n="http://www.sped.fazenda.gov.br/nfse"><n:infDPS Id="x"/></n:DPS>')
        with self.assertRaisesRegex(ValueError, "E1228"):
            signing.sign(prefixed, self.cert)

    def test_cancellation_signs(self):
        el = events.build_cancellation(ACCESS_KEY, "1", "Valor do serviço informado errado", PRODUCAO, author_cnpj="11222333000181")
        root = signing.sign(el, self.cert)
        signing.verify(root)
        schemas.validate(root, PRODUCAO.layout)


if __name__ == "__main__":
    unittest.main()
