"""Regression cases from the public release readiness review."""
import base64
import copy
import gzip
import http.client
import json
import ssl
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest import mock

from lxml import etree

from nfse_nacional import documents, schemas, signing
from nfse_nacional.client import AdnClient, Response, SefinClient, _Http, _transport_error, _retry_after, decode_xml
from nfse_nacional.dps import FederalTax, MunicipalTax, TotalTax, build_dps
from nfse_nacional.environment import PRODUCAO, PRODUCAO_RESTRITA
from nfse_nacional.errors import TransportError
from nfse_nacional.pdf_fonts import FontConfigurationError, FontSet
from test_danfse import make_nfse, SAMPLE
from test_dps import me_epp_dps
from test_pdf_fonts import with_customer_name
from nfse_nacional.danfse import render_danfse


class ReadinessTests(unittest.TestCase):
    def test_adn_documents_batch_must_advance_cursor(self):
        client = AdnClient(ssl.create_default_context(), PRODUCAO_RESTRITA)
        for fields in ({}, {'LoteDFe': []}, {'LoteDFe': [{'NSU': 10}]}):
            body = {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', **fields}
            with self.subTest(fields=fields), mock.patch.object(client._http, 'request', return_value=Response(200, json.dumps(body).encode(), {})):
                with self.assertRaises(TransportError):
                    client.fetch_dfe(10)

    def test_total_tax_tuples_refuse_floats_and_preserve_decimal_values(self):
        base = me_epp_dps()
        provider = replace(base.prest, opSimpNac='1', regApTribSN=None)
        for env in (PRODUCAO, PRODUCAO_RESTRITA):
            for factory in (TotalTax.percent, TotalTax.amount):
                for i in range(3):
                    values = [Decimal('1.00')] * 3
                    values[i] = 2.5
                    with self.subTest(layout=env.layout, factory=factory.__name__, position=i):
                        with self.assertRaisesRegex(TypeError, 'Decimal'):
                            build_dps(replace(base, prest=provider, totTrib=factory(*values)), env)
                valid = replace(base, prest=provider, totTrib=factory(Decimal('2.50'), 1, '0.00'))
                schemas.validate(build_dps(valid, env), env.layout)

    def test_empty_and_non_gzip_documents_are_not_success(self):
        for value in ('AAAA', '', base64.b64encode(gzip.compress(b'')).decode()):
            with self.subTest(value=value), self.assertRaises(TransportError):
                decode_xml(value)
        client = AdnClient(ssl.create_default_context(), PRODUCAO_RESTRITA)
        body = {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', 'LoteDFe': [{'NSU': 1, 'ArquivoXml': 'AAAA'}]}
        with mock.patch.object(client._http, 'request', return_value=Response(200, json.dumps(body).encode(), {})):
            with self.assertRaises(TransportError):
                client.fetch_dfe(0)

    def test_proxy_404_cannot_trigger_emission_replay(self):
        client = SefinClient(ssl.create_default_context(), PRODUCAO_RESTRITA)
        with mock.patch.object(client._http, 'request', return_value=Response(404, b'<html>proxy</html>', {})) as request:
            with self.assertRaises(TransportError):
                client.recover(b'<DPS/>', 'saved-id')
            self.assertEqual(request.call_count, 1)

    def test_retry_after_must_be_finite(self):
        for value in ('inf', 'nan', '1e309'):
            self.assertIsNone(_retry_after(value))

    def test_float_amounts_are_refused_in_both_layouts(self):
        for env in (PRODUCAO, PRODUCAO_RESTRITA):
            for note in (replace(me_epp_dps(), vServ=2.25), replace(me_epp_dps(), tribMun=MunicipalTax(pAliq=2.25))):
                with self.subTest(layout=env.layout), self.assertRaisesRegex(TypeError, 'Decimal'):
                    build_dps(note, env)

    def test_missing_certificate_or_signature_value_raises_value_error(self):
        path = Path(__file__).parent / 'fixtures/pynfsenacional_golden_dps_signed.xml'
        for tag in ('SignatureValue', 'KeyInfo'):
            root = etree.parse(str(path)).getroot()
            sig = root[-1]
            sig.remove(sig.find('{' + signing.DSIG_NS + '}' + tag))
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'missing signature'):
                signing.verify(root)

    def test_wrapped_independent_dps_is_rejected(self):
        root = etree.parse(str(Path(__file__).parent / 'fixtures/pynfsenacional_golden_dps_signed.xml')).getroot()
        signing.verify(root)
        original = etree.fromstring(etree.tostring(root[0]))
        root[0].set('Id', root[0].get('Id')[:-1] + '2')
        root[0].find('.//{http://www.sped.fazenda.gov.br/nfse}vServ').text = '1.00'
        obj = etree.SubElement(root[-1], '{' + signing.DSIG_NS + '}Object')
        obj.append(original)
        schemas.validate(root, PRODUCAO.layout)
        with self.assertRaisesRegex(ValueError, 'primary fiscal'):
            signing.verify(root)

    def test_nfse_reference_cannot_sign_a_hidden_payload(self):
        root = etree.fromstring(make_nfse(SAMPLE))
        signing.verify(root)
        original = etree.fromstring(etree.tostring(root[0]))
        root[0].set('Id', root[0].get('Id')[:-1] + '2')
        root[0].find('{http://www.sped.fazenda.gov.br/nfse}nNFSe').text = '999999'
        etree.SubElement(root[-1], '{' + signing.DSIG_NS + '}Object').append(original)
        schemas.validate(root, PRODUCAO.layout)
        with self.assertRaisesRegex(ValueError, 'primary fiscal'):
            signing.verify(root)

    def test_duplicate_reference_id_and_missing_signed_info_are_rejected(self):
        path = Path(__file__).parent / 'fixtures/pynfsenacional_golden_dps_signed.xml'
        root = etree.parse(str(path)).getroot()
        etree.SubElement(root[-1], '{' + signing.DSIG_NS + '}Object').append(copy.deepcopy(root[0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            signing.verify(root)
        root = etree.parse(str(path)).getroot()
        root[-1].remove(root[-1][0])
        with self.assertRaisesRegex(ValueError, 'SignedInfo'):
            signing.verify(root)

    def test_corrupt_emission_response_remains_recoverable(self):
        client = SefinClient(ssl.create_default_context(), PRODUCAO_RESTRITA)
        for data in ('not-base64!', base64.b64encode(gzip.compress(b'<NFSe/>')[:-5]).decode()):
            with self.subTest(data=data), mock.patch.object(client._http, 'request', return_value=Response(
                    201, json.dumps({'chaveAcesso': 'test', 'nfseXmlGZipB64': data}).encode(), {})):
                with self.assertRaises(TransportError) as ctx:
                    client.emit(b'<DPS/>')
                self.assertIsNotNone(ctx.exception.__cause__)

    def test_partial_http_reads_are_transport_errors(self):
        transport = _Http('https://example.test', ssl.create_default_context(), 1)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = http.client.IncompleteRead(b'private')
        with mock.patch.object(transport._opener, 'open', return_value=response):
            with self.assertRaises(TransportError) as ctx:
                transport.request('POST', '/nfse')
        self.assertNotIn('private', str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, http.client.IncompleteRead)

    def test_adn_failure_cannot_mean_exhausted(self):
        client = AdnClient(ssl.create_default_context(), PRODUCAO_RESTRITA)
        cases = [
            (503, {'StatusProcessamento': 'NENHUM_DOCUMENTO_LOCALIZADO', 'LoteDFe': []}),
            (200, {'StatusProcessamento': None}),
            (200, {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', 'LoteDFe': [{'NSU': 'oops'}]}),
            (200, {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', 'LoteDFe': [None]}),
            (200, {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', 'LoteDFe': {'NSU': 1}}),
            (200, {'StatusProcessamento': 'DOCUMENTOS_LOCALIZADOS', 'LoteDFe': [{'NSU': True}]}),
        ]
        for status, body in cases:
            with self.subTest(body=body), mock.patch.object(client._http, 'request', return_value=Response(status, json.dumps(body).encode(), {})):
                with self.assertRaises(TransportError):
                    client.fetch_dfe(0)

    def test_pis_cofins_fields_require_cst_in_both_layouts(self):
        for env in (PRODUCAO, PRODUCAO_RESTRITA):
            for field in ('vBCPisCofins', 'pAliqPis', 'pAliqCofins', 'vPis', 'vCofins', 'tpRetPisCofins'):
                value = '1' if field == 'tpRetPisCofins' else Decimal('1.00')
                with self.subTest(layout=env.layout, field=field):
                    note = replace(me_epp_dps(), tribFed=FederalTax(**{field: value}))
                    with self.assertRaisesRegex(ValueError, 'require CST'):
                        build_dps(note, env)
            schemas.validate(build_dps(replace(me_epp_dps(), tribFed=FederalTax(vRetIRRF=Decimal('1.00'))), env), env.layout)

    def test_winansi_punctuation_and_undefined_controls(self):
        text = 'Consultoria — sistema € com apóstrofo ’'
        self.assertEqual(FontSet().runs(text, 'Helvetica'), [('Helvetica', text)])
        self.assertTrue(render_danfse(with_customer_name(text)).startswith(b'%PDF-'))
        for char in ('\x81', '\x7f'):
            with self.subTest(char=repr(char)), self.assertRaises(FontConfigurationError):
                FontSet().runs(char, 'Helvetica')

    def test_document_validators_reject_trailing_newline(self):
        self.assertFalse(documents.is_valid_cnpj('11222333000181\n'))
        self.assertFalse(documents.is_valid_cpf('11144477735\n'))

    def test_retry_after_header_case_is_irrelevant(self):
        for header in ('Retry-After', 'retry-after', 'RETRY-AFTER'):
            self.assertEqual(_transport_error(Response(503, b'', {header: '7'}), 'emit').retry_after, 7)
