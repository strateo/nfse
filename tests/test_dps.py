import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from lxml import etree

from nfse_nacional import dps as d
from nfse_nacional import events, schemas
from nfse_nacional.environment import PRODUCAO, PRODUCAO_RESTRITA

NS = {"n": d.NFSE_NS}
ENVIRONMENTS = (PRODUCAO, PRODUCAO_RESTRITA)
ACCESS_KEY = "3550308" + "1" + "2" + "11222333000181" + "0" * 27


def me_epp_dps(**changes) -> d.Dps:
    base = d.Dps(
        cLocEmi="3550308",
        serie="900",
        nDPS="1",
        dCompet=date(2026, 11, 3),
        dhEmi=datetime(2026, 11, 3, 10, 15, 0, tzinfo=d.BRASILIA),
        prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_ME_EPP, CNPJ="11222333000181", regApTribSN="1", IM="12345"),
        serv=d.Service(cTribNac="010101", xDescServ="Consultoria em tecnologia da informação", cLocPrestacao="3550308", cNBS="115022000"),
        vServ=Decimal("1500"),
        tribMun=d.MunicipalTax(pAliq=Decimal("2.5")),
        totTrib=d.TotalTax.simples(Decimal("6")),
        toma=d.Taker(
            xNome="Acme Serviços Ltda",
            CNPJ="11444777000161",
            end=d.Address(xLgr="Av. Paulista", nro="1000", xBairro="Bela Vista", cMun="3550308", CEP="01310100"),
            email="fiscal@acme.example",
        ),
    )
    return replace(base, **changes)


def text(el, path):
    return el.findtext(path, namespaces=NS)


class DpsXsdTest(unittest.TestCase):
    def assert_valid_everywhere(self, dps):
        for env in ENVIRONMENTS:
            with self.subTest(env=env.name):
                schemas.validate(d.build_dps(dps, env), env.layout)

    def test_me_epp_valid_in_both_layouts(self):
        self.assert_valid_everywhere(me_epp_dps())

    def test_mei_valid_in_both_layouts(self):
        self.assert_valid_everywhere(
            me_epp_dps(
                prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_MEI, CPF="52998224725"),
                tribMun=d.MunicipalTax(),
                totTrib=d.TotalTax.mei(),
                toma=None,
            )
        )

    def test_nao_optante_with_federal_taxes(self):
        self.assert_valid_everywhere(
            me_epp_dps(
                prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_NAO_OPTANTE, CNPJ="11222333000181"),
                totTrib=d.TotalTax.percent(Decimal("13.45"), Decimal("0"), Decimal("2.5")),
                tribFed=d.FederalTax(CST="01", vBCPisCofins=Decimal("1500"), pAliqPis=Decimal("0.65"),
                                     pAliqCofins=Decimal("3"), vPis=Decimal("9.75"), vCofins=Decimal("45"),
                                     vRetIRRF=Decimal("22.50")),
            )
        )

    def test_ibscbs_and_substitution(self):
        self.assert_valid_everywhere(
            me_epp_dps(
                ibscbs=d.IbsCbs(cIndOp="100301", CST="000", cClassTrib="000001"),
                subst=d.Substitution(chSubstda=ACCESS_KEY, cMotivo="99", xMotivo="Valor do serviço informado errado"),
            )
        )

    def test_recipient_branches_and_alphanumeric_cnpj_in_both_layouts(self):
        foreign_address = d.Address(
            xLgr="10 Downing Street", nro="10", xBairro="Westminster",
            cPais="GB", cEndPost="SW1A 2AA", xCidade="London", xEstProvReg="England",
        )
        recipients = (
            # A Brazilian individual may omit an address in the base layout.
            d.Taker(xNome="Pessoa Física", CPF="52998224725"),
            d.Taker(xNome="Empresa", CNPJ="12ABC34501DE35", end=me_epp_dps().toma.end),
            d.Taker(xNome="李明", NIF="AB-123456", end=foreign_address),
            d.Taker(xNome="Jane Doe", cNaoNIF="1", end=foreign_address),
            d.Taker(xNome="John Doe", cNaoNIF="2", end=foreign_address),
        )
        for recipient in recipients:
            with self.subTest(recipient_type=next(
                key for key in ("CPF", "CNPJ", "NIF", "cNaoNIF")
                if getattr(recipient, key) is not None
            )):
                self.assert_valid_everywhere(me_epp_dps(toma=recipient))


class LayoutTest(unittest.TestCase):
    def test_finnfse_and_xdescserv_position(self):
        g = d.IbsCbs(cIndOp="100301", CST="000", cClassTrib="000001")
        prod = d.build_dps(me_epp_dps(ibscbs=g), PRODUCAO)
        rest = d.build_dps(me_epp_dps(ibscbs=g), PRODUCAO_RESTRITA)
        self.assertIsNone(prod.find("n:infDPS/n:finNFSe", NS))
        self.assertEqual(text(prod, "n:infDPS/n:IBSCBS/n:finNFSe"), "0")
        self.assertEqual(text(prod, "n:infDPS/n:IBSCBS/n:valores/n:trib/n:gIBSCBS/n:CST"), "000")
        self.assertEqual(text(rest, "n:infDPS/n:finNFSe"), "0")
        self.assertEqual(text(rest, "n:infDPS/n:IBSCBS/n:valores/n:trib/n:CST"), "000")
        prod_cserv = [etree.QName(e).localname for e in prod.find("n:infDPS/n:serv/n:cServ", NS)]
        rest_cserv = [etree.QName(e).localname for e in rest.find("n:infDPS/n:serv/n:cServ", NS)]
        self.assertEqual(prod_cserv, ["cTribNac", "xDescServ", "cNBS"])
        self.assertEqual(rest_cserv, ["cTribNac", "cNBS", "xDescServ"])

    def test_formats(self):
        el = d.build_dps(me_epp_dps(), PRODUCAO)
        self.assertEqual(el.get("versao"), "1.01")
        self.assertEqual(el.nsmap, {None: d.NFSE_NS})
        self.assertEqual(text(el, "n:infDPS/n:dhEmi"), "2026-11-03T10:15:00-03:00")
        self.assertEqual(text(el, "n:infDPS/n:valores/n:vServPrest/n:vServ"), "1500.00")
        self.assertEqual(text(el, "n:infDPS/n:valores/n:trib/n:tribMun/n:pAliq"), "2.50")
        self.assertEqual(text(el, "n:infDPS/n:tpAmb"), "1")
        self.assertIsNone(el.find("n:infDPS/n:prest/n:xNome", NS))

    def test_default_dhemi_is_brasilia_and_backdated(self):
        el = d.build_dps(me_epp_dps(dhEmi=None), PRODUCAO)
        value = datetime.fromisoformat(text(el, "n:infDPS/n:dhEmi"))
        self.assertEqual(value.utcoffset(), d.BRASILIA.utcoffset(None))
        self.assertLess(value, datetime.now(d.BRASILIA))


class GuardTest(unittest.TestCase):
    def assert_rejected(self, dps, fragment):
        with self.assertRaises(ValueError) as ctx:
            d.build_dps(dps, PRODUCAO)
        self.assertIn(fragment, str(ctx.exception))

    def test_regime_rules(self):
        self.assert_rejected(me_epp_dps(totTrib=d.TotalTax.mei()), "E0712")
        self.assert_rejected(
            me_epp_dps(prest=d.Provider(opSimpNac="3", CNPJ="11222333000181")), "E0166"
        )
        self.assert_rejected(
            me_epp_dps(prest=d.Provider(opSimpNac="2", CNPJ="11222333000181"), tribMun=d.MunicipalTax(),
                       tribFed=d.FederalTax(vRetIRRF=Decimal("1")), totTrib=d.TotalTax.mei()),
            "E0676",
        )

    def test_service_and_party_rules(self):
        self.assert_rejected(me_epp_dps(ibscbs=d.IbsCbs(cIndOp="1", CST="000", cClassTrib="000001"),
                                        serv=d.Service(cTribNac="010101", xDescServ="x", cLocPrestacao="3550308")), "E0322")
        self.assert_rejected(me_epp_dps(toma=d.Taker(xNome="Self", CNPJ="11222333000181")), "E0202")
        self.assert_rejected(me_epp_dps(tribMun=d.MunicipalTax(pAliq=Decimal("25"))), "E0595")

    def test_required_recipient_addresses_are_guarded_without_repeating_pii(self):
        cases = (
            (d.Taker(xNome="Private buyer", CNPJ="11444777000161"), "E0235"),
            (d.Taker(xNome="Private buyer", NIF="sensitive-identifier"), "E0242"),
        )
        for recipient, expected in cases:
            for env in ENVIRONMENTS:
                with self.subTest(rule=expected, layout=env.layout):
                    with self.assertRaises(ValueError) as ctx:
                        d.build_dps(me_epp_dps(toma=recipient), env)
                    self.assertIn(expected, str(ctx.exception))
                    self.assertNotIn("sensitive-identifier", str(ctx.exception))

    def test_base_library_keeps_no_nif_source_document_compatibility(self):
        # New-checkout address/absence-reason policy belongs to billing.map_billing.
        for env in ENVIRONMENTS:
            schemas.validate(d.build_dps(me_epp_dps(toma=d.Taker(xNome="Source buyer", cNaoNIF="0")), env), env.layout)

    def test_invalid_recipient_identifier_error_does_not_echo_identifier(self):
        with self.assertRaises(ValueError) as ctx:
            d.build_dps(
                me_epp_dps(toma=d.Taker(xNome="Private buyer", CPF="00000000000")),
                PRODUCAO,
            )
        self.assertIn("invalid toma CPF", str(ctx.exception))
        self.assertNotIn("00000000000", str(ctx.exception))

    def test_decimal_formatting_refuses_lossy_values(self):
        self.assertEqual(d.format_decimal(Decimal("51.6")), "51.60")
        self.assertEqual(d.format_decimal(0), "0.00")
        for bad in (Decimal("0.025"), Decimal("-1"), Decimal("NaN")):
            with self.assertRaises(ValueError):
                d.format_decimal(bad)
        with self.assertRaises(TypeError):
            d.format_decimal(1.5)


class CancellationTest(unittest.TestCase):
    def test_valid_in_both_layouts(self):
        for env in ENVIRONMENTS:
            with self.subTest(env=env.name):
                el = events.build_cancellation(
                    ACCESS_KEY, events.CANCEL_REASON_ISSUE_ERROR, "Valor do serviço informado errado", env,
                    author_cnpj="11222333000181",
                    dh_evento=datetime(2026, 11, 3, 11, 0, tzinfo=d.BRASILIA),
                )
                schemas.validate(el, env.layout)
                self.assertEqual(el.find("n:infPedReg", NS).get("Id"), "PRE" + ACCESS_KEY + "101101")

    def test_reason_length(self):
        with self.assertRaises(ValueError):
            events.build_cancellation(ACCESS_KEY, "9", "curto", PRODUCAO, author_cnpj="11222333000181")


if __name__ == "__main__":
    unittest.main()
