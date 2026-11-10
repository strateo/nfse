"""Ensure the production compatibility fix does not weaken other numeric types."""
import unittest
from importlib import resources

from lxml import etree

from nfse_nacional import schemas
from nfse_nacional.environment import Layout, PRODUCAO, PRODUCAO_RESTRITA


class SchemaCompatibilityTest(unittest.TestCase):
    def test_release_one_alias_preserves_existing_layout_identity(self):
        self.assertIs(Layout.NT009_R1, Layout.NT009)
        self.assertIs(Layout("nt009"), Layout.NT009_R1)
        self.assertEqual(Layout.NT009.name, "NT009")
        self.assertEqual(len(Layout), 2)
        self.assertIs(PRODUCAO_RESTRITA.layout, Layout.NT009_R1)
        self.assertEqual(PRODUCAO_RESTRITA.layout.release, "nt009-r1-20261020")
        self.assertEqual(PRODUCAO.layout.release, "v1.01-20260209")

    def test_alphanumeric_fix_is_scoped_to_cnpj_type(self):
        source = resources.files("nfse_nacional") / "schemas" / "producao" / "tiposSimples_v1.01.xsd"
        with resources.as_file(source) as path:
            for name in ("TSCNPJ", "TSCAEPF", "TSNumBeneficioMunicipal", "TSNum14Dig"):
                with self.subTest(type=name):
                    wrapper = f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                        xmlns:n="http://www.sped.fazenda.gov.br/nfse"
                        targetNamespace="http://www.sped.fazenda.gov.br/nfse">
                        <xs:include schemaLocation="{path.as_uri()}"/>
                        <xs:element name="probe" type="n:{name}"/></xs:schema>'''
                    parser = etree.XMLParser(no_network=True)
                    parser.resolvers.add(schemas._OfficialSchemaFixes())
                    schema = etree.XMLSchema(etree.fromstring(wrapper.encode(), parser))
                    probe = etree.Element("{http://www.sped.fazenda.gov.br/nfse}probe")
                    probe.text = "12ABC34501DE35"
                    self.assertEqual(schema.validate(probe), name == "TSCNPJ")
                    probe.text = "11444777000161"
                    self.assertTrue(schema.validate(probe))
                    if name == "TSCNPJ":
                        for bad in ("12abc34501de35", "12ABC34501DEAB", "ABC"):
                            probe.text = bad
                            self.assertFalse(schema.validate(probe))
