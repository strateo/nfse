import base64
import re
import unittest
import zlib
from dataclasses import replace
from decimal import Decimal

from lxml import etree

from _certs import PFX_PASSWORD, make_pki
from nfse_nacional import danfse, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional import dps as d
from nfse_nacional.environment import PRODUCAO, PRODUCAO_RESTRITA, Environment
from test_dps import ACCESS_KEY, me_epp_dps

N = d.NFSE_NS
SEFIN_CERT = Certificate.from_pfx(make_pki().client_pfx, PFX_PASSWORD)


def _el(parent, tag, text=None):
    el = etree.SubElement(parent, f"{{{N}}}{tag}")
    if text is not None:
        el.text = text
    return el


def make_nfse(dps: d.Dps, env: Environment = PRODUCAO, ibscbs: bool = True) -> bytes:
    """A synthetic authorized NFS-e wrapping a DPS from our builder, valid against NFSe_v1.01.xsd."""
    root = etree.Element(f"{{{N}}}NFSe", nsmap={None: N}, versao="1.01")
    inf = etree.SubElement(root, f"{{{N}}}infNFSe", Id="NFS" + ACCESS_KEY)
    for tag, text in [("xLocEmi", "São Paulo"), ("xLocPrestacao", "São Paulo"), ("nNFSe", "147"),
                      ("cLocIncid", "3550308"), ("xLocIncid", "São Paulo"),
                      ("xTribNac", "Análise e desenvolvimento de sistemas."), ("xNBS", "Serviços de consultoria em TI"),
                      ("verAplic", "SefinNac_1.6.0"), ("ambGer", "2"), ("tpEmis", "1"), ("procEmi", "1"),
                      ("cStat", "100"), ("dhProc", "2026-11-03T10:15:07-03:00"), ("nDFSe", "998877")]:
        _el(inf, tag, text)
    emit = _el(inf, "emit")
    _el(emit, "CNPJ", "11222333000181")
    _el(emit, "IM", "12345")
    _el(emit, "xNome", "Empresa Teste de Software Ltda")
    end = _el(emit, "enderNac")
    for tag, text in [("xLgr", "Rua Augusta"), ("nro", "500"), ("xCpl", "Sala 12"), ("xBairro", "Consolação"),
                      ("cMun", "3550308"), ("UF", "SP"), ("CEP", "01305000")]:
        _el(end, tag, text)
    _el(emit, "email", "fiscal@empresa.example")
    valores = _el(inf, "valores")
    for tag, text in [("vBC", "1500.00"), ("pAliqAplic", "2.50"), ("vISSQN", "37.50"), ("vTotalRet", "0.00"), ("vLiq", "1500.00")]:
        _el(valores, tag, text)
    if ibscbs:
        g = _el(inf, "IBSCBS")
        _el(g, "cLocalidadeIncid", "3550308")
        _el(g, "xLocalidadeIncid", "São Paulo")
        v = _el(g, "valores")
        _el(v, "vBC", "1462.50")
        for group, fields in [("uf", [("pIBSUF", "0.10"), ("pAliqEfetUF", "0.10")]),
                              ("mun", [("pIBSMun", "0.00"), ("pAliqEfetMun", "0.00")]),
                              ("fed", [("pCBS", "0.90"), ("pAliqEfetCBS", "0.90")])]:
            grp = _el(v, group)
            for tag, text in fields:
                _el(grp, tag, text)
        tot = _el(g, "totCIBS")
        _el(tot, "vTotNF", "1514.63")
        gibs = _el(tot, "gIBS")
        _el(gibs, "vIBSTot", "1.46")
        _el(_el(gibs, "gIBSUFTot"), "vIBSUF", "1.46")
        _el(_el(gibs, "gIBSMunTot"), "vIBSMun", "0.00")
        _el(_el(tot, "gCBS"), "vCBS", "13.17")
    inf.append(d.build_dps(dps, env))
    signing.sign(root, SEFIN_CERT)  # SEFIN signs infNFSe; the XSD requires the Signature
    return signing.to_bytes(root)


def pdf_text(pdf: bytes) -> str:
    """Concatenate the text-showing strings of every content stream (WinAnsi-encoded)."""
    out = []
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        try:
            data = zlib.decompress(base64.a85decode(stream.strip().removesuffix(b"~>")))
        except (ValueError, zlib.error):
            continue
        for raw in re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", data, re.S):
            unescaped = re.sub(rb"\\([0-7]{1,3}|.)", lambda m: bytes([int(m.group(1), 8)]) if m.group(1).isdigit() else m.group(1), raw)
            out.append(unescaped.decode("cp1252"))
    return "\n".join(out)


def page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page[^s]", pdf))


SAMPLE = me_epp_dps(
    ibscbs=d.IbsCbs(cIndOp="100301", CST="000", cClassTrib="000001"),
    serv=d.Service(cTribNac="010101", xDescServ="Desenvolvimento do módulo fiscal\nSprint de outubro",
                   cLocPrestacao="3550308", cNBS="115022000", xInfComp="Pedido 4521"),
)


class DanfseTest(unittest.TestCase):
    def test_fixture_is_schema_valid(self):
        for env in (PRODUCAO, PRODUCAO_RESTRITA):
            with self.subTest(env=env.name):
                schemas.validate(etree.fromstring(make_nfse(SAMPLE, env)), env.layout)

    def test_renders_one_a4_page_with_core_fields(self):
        pdf = danfse.render_danfse(make_nfse(SAMPLE))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(page_count(pdf), 1)
        self.assertIn(b"/MediaBox [ 0 0 595.2756 841.8898 ]", pdf)
        text = pdf_text(pdf)
        for expected in [
            "DANFSe v2.0", "Documento Auxiliar da NFS-e", "Município: São Paulo - SP",
            "Ambiente Gerador: Sistema Nacional da NFS-e", "Tipo de Ambiente: Produção",
            ACCESS_KEY, "147", "03/11/2026", "03/11/2026 10:15:07", "Prestador", "NFS-e Gerada", "NFS-e regular",
            "11.222.333/0001-81", "Empresa Teste de Software Ltda", "Rua Augusta, 500, Sala 12, Consolação",
            "3550308 / 01.305-000",
            "Optante - Microempresa ou Empresa de P...",  # NT 008: ellipsis when the description overflows
            "11.444.777/0001-61", "Acme Serviços Ltda", "São Paulo / SP", "Av. Paulista, 1000, Bela Vista",
            "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO",
            "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
            "01.01.01", "1.1502.20.00", "São Paulo / SP / BR", "Análise e desenvolvimento de sistemas.",
            "Desenvolvimento do módulo fiscal", "Sprint de outubro",
            "Operação Tributável", "R$ 1.500,00", "2,50%", "R$ 37,50", "Não Retido",
            "000 / 000001", "100301 / 3550308 / São Paulo / SP", "R$ 1.462,50", "R$ 14,63", "R$ 1.514,63",
            "Inf. Cont.: Pedido 4521",
            "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: Simples Nacional: 6,00%",
            f"147 / {ACCESS_KEY}",
        ]:
            self.assertIn(expected, text)
        self.assertNotIn("SEM VALIDADE", text)
        self.assertNotIn("CANCELADA", text)

    def test_homologation_banner_and_watermarks(self):
        test_pdf = danfse.render_danfse(make_nfse(SAMPLE, PRODUCAO_RESTRITA))
        self.assertIn("NFS-e SEM VALIDADE JURÍDICA", pdf_text(test_pdf))
        self.assertIn("Tipo de Ambiente: Homologação", pdf_text(test_pdf))
        for mark in danfse.Watermark:
            with self.subTest(mark=mark):
                self.assertIn(mark.value, pdf_text(danfse.render_danfse(make_nfse(SAMPLE), mark)))

    def test_missing_groups_print_notices_and_dashes(self):
        bare = replace(SAMPLE, toma=None, ibscbs=None,
                       serv=replace(SAMPLE.serv, cNBS=None, xInfComp=None))
        text = pdf_text(danfse.render_danfse(make_nfse(bare, ibscbs=False)))
        self.assertIn("TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", text)
        self.assertIn("DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", text)
        self.assertIn("-", text.splitlines())

    def test_retained_pis_cofins_add_to_social_contributions(self):
        retained = replace(
            SAMPLE,
            prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_NAO_OPTANTE, CNPJ="11222333000181"),
            totTrib=d.TotalTax.amount(Decimal("100"), Decimal("0"), Decimal("37.50")),
            tribFed=d.FederalTax(CST="01", vBCPisCofins=Decimal("1500"), pAliqPis=Decimal("0.65"), pAliqCofins=Decimal("3"),
                                 vPis=Decimal("9.75"), vCofins=Decimal("45"), tpRetPisCofins="1", vRetCSLL=Decimal("15")),
        )
        text = pdf_text(danfse.render_danfse(make_nfse(retained)))
        self.assertIn("R$ 69,75", text)  # vRetCSLL + vPis + vCofins
        self.assertIn("PIS/COFINS Retidos", text)
        self.assertIn("Federais: R$ 100,00; Estaduais: R$ 0,00; Municipais: R$ 37,50", text)

    def test_long_text_stays_on_one_page(self):
        long = replace(SAMPLE, serv=replace(SAMPLE.serv, xDescServ="Serviço " * 240, xInfComp="Observação " * 180))
        pdf = danfse.render_danfse(make_nfse(long))
        self.assertEqual(page_count(pdf), 1)
        self.assertIn("Totais Aproximados dos Tributos", pdf_text(pdf))

    def test_nt009_layout(self):
        text = pdf_text(danfse.render_danfse(make_nfse(SAMPLE, PRODUCAO_RESTRITA)))
        self.assertIn("NFS-e regular", text)
        self.assertIn("000 / 000001", text)

    def test_qr_url_and_input_check(self):
        self.assertEqual(danfse.consulta_url(ACCESS_KEY), "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=" + ACCESS_KEY)
        with self.assertRaises(ValueError):
            danfse.render_danfse(b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse"/>')

    def test_formatting(self):
        self.assertEqual(danfse.money("1234567.8"), "R$ 1.234.567,80")
        self.assertEqual(danfse.document("52998224725"), "529.982.247-25")
        self.assertEqual(danfse.document("12ABC34501DE35"), "12.ABC.345/01DE-35")
        self.assertEqual(danfse.datetime_br("2026-11-03T10:15:07-03:00"), "03/11/2026 10:15:07")


if __name__ == "__main__":
    unittest.main()
