import base64
import gzip
import json
import shutil
import ssl
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _certs import PFX_PASSWORD, make_pki
from nfse_nacional.certificate import Certificate
from nfse_nacional.client import AdnClient, SefinClient, decode_xml, encode_xml
from nfse_nacional.environment import PRODUCAO_RESTRITA
from nfse_nacional.errors import RejectionError, TransportError

DPS_ID = "DPS3550308" + "2" + "11222333000181" + "00900" + "0" * 14 + "1"
KEY = "3550308" + "1" + "2" + "11222333000181" + "0" * 27
NFSE_XML = b'<?xml version="1.0" encoding="UTF-8"?><NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"/>'


class FakeServer:
    """mTLS HTTPS server answering from a {(method, path): (status, body, headers)} table."""

    def __init__(self, pki, tmp: Path):
        (tmp / "ca.pem").write_bytes(pki.ca_pem)
        (tmp / "server.pem").write_bytes(pki.server_cert_pem + pki.server_key_pem)
        self.routes: dict[tuple[str, str], tuple[int, bytes, dict]] = {}
        self.requests: list[tuple[str, str, dict, bytes]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                owner.requests.append((self.command, self.path, dict(self.headers), body))
                status, payload, headers = owner.routes.get((self.command, self.path), (500, b"no route", {}))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            do_GET = do_POST = do_HEAD = _serve

            def log_message(self, *args):
                pass

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(tmp / "server.pem"))
        ctx.load_verify_locations(str(tmp / "ca.pem"))
        ctx.verify_mode = ssl.CERT_REQUIRED
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"https://127.0.0.1:{self.httpd.server_address[1]}"

    def json(self, method, path, status, body, headers=None):
        self.routes[(method, path)] = (status, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp(prefix="nfse-test-"))
        cls.addClassCleanup(shutil.rmtree, tmp)
        pki = make_pki()
        cls.server = FakeServer(pki, tmp)
        cls.addClassCleanup(cls.server.stop)
        cert = Certificate.from_pfx(pki.client_pfx, PFX_PASSWORD)
        cls.ca_file = str(tmp / "ca.pem")
        cls.context = cert.ssl_context(cafile=cls.ca_file)
        env = replace(PRODUCAO_RESTRITA, sefin_url=cls.server.base + "/SefinNacional", adn_url=cls.server.base + "/contribuintes")
        cls.sefin = SefinClient(cls.context, env, timeout=10)
        cls.adn = AdnClient(cls.context, env, timeout=10)

    def setUp(self):
        self.server.routes.clear()
        self.server.requests.clear()

    def test_emit_success_sends_gzip_base64_envelope(self):
        self.server.json("POST", "/SefinNacional/nfse", 201, {
            "tipoAmbiente": 2, "idDps": DPS_ID, "chaveAcesso": KEY,
            "nfseXmlGZipB64": encode_xml(NFSE_XML), "alertas": [{"codigo": "A001", "descricao": "aviso"}],
        })
        result = self.sefin.emit(b"<DPS/>")
        self.assertEqual(result.access_key, KEY)
        self.assertEqual(result.nfse_xml, NFSE_XML)
        self.assertEqual(result.alerts[0].code, "A001")
        method, path, headers, body = self.server.requests[0]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(gzip.decompress(base64.b64decode(json.loads(body)["dpsXmlGZipB64"])), b"<DPS/>")

    def test_emit_rejection_carries_codes(self):
        self.server.json("POST", "/SefinNacional/nfse", 400, {
            "idDPS": DPS_ID, "erros": [{"Codigo": "E0014", "Descricao": "DPS já utilizada", "Complemento": None}],
        })
        with self.assertRaises(RejectionError) as ctx:
            self.sefin.emit(b"<DPS/>")
        self.assertEqual(ctx.exception.codes, ["E0014"])
        self.assertFalse(ctx.exception.transient)

    def test_maintenance_rejection_is_transient(self):
        self.server.json("POST", "/SefinNacional/nfse", 400, {"erros": [{"codigo": "E1217", "descricao": "Serviço paralisado"}]})
        with self.assertRaises(RejectionError) as ctx:
            self.sefin.emit(b"<DPS/>")
        self.assertTrue(ctx.exception.transient)

    def test_gateway_error_is_transient_transport_error(self):
        self.server.routes[("POST", "/SefinNacional/nfse")] = (503, b"<html>busy</html>", {"Retry-After": "7"})
        with self.assertRaises(TransportError) as ctx:
            self.sefin.emit(b"<DPS/>")
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(ctx.exception.retry_after, 7.0)

    def test_recover_returns_existing_note_without_resending(self):
        self.server.json("GET", f"/SefinNacional/dps/{DPS_ID}", 200, {"idDps": DPS_ID, "chaveAcesso": KEY})
        self.server.json("GET", f"/SefinNacional/nfse/{KEY}", 200, {"chaveAcesso": KEY, "nfseXmlGZipB64": encode_xml(NFSE_XML)})
        result = self.sefin.recover(b"<DPS/>", DPS_ID)
        self.assertEqual((result.access_key, result.nfse_xml), (KEY, NFSE_XML))
        self.assertNotIn("POST", [r[0] for r in self.server.requests])

    def test_recover_resends_same_document_when_absent(self):
        self.server.json("GET", f"/SefinNacional/dps/{DPS_ID}", 404, {"erro": [{"codigo": "E404", "descricao": "não encontrada"}]})
        self.server.json("POST", "/SefinNacional/nfse", 201, {"chaveAcesso": KEY, "nfseXmlGZipB64": encode_xml(NFSE_XML)})
        self.assertEqual(self.sefin.recover(b"<DPS same/>", DPS_ID).access_key, KEY)
        body = json.loads(self.server.requests[-1][3])
        self.assertEqual(decode_xml(body["dpsXmlGZipB64"]), b"<DPS same/>")

    def test_dps_exists_uses_head(self):
        self.server.routes[("HEAD", f"/SefinNacional/dps/{DPS_ID}")] = (200, b"", {})
        self.assertTrue(self.sefin.dps_exists(DPS_ID))
        self.server.routes[("HEAD", f"/SefinNacional/dps/{DPS_ID}")] = (404, b"", {})
        self.assertFalse(self.sefin.dps_exists(DPS_ID))

    def test_event_accepts_double_base64_and_error_list(self):
        double = base64.b64encode(encode_xml(b"<evento/>").encode()).decode()
        self.server.json("POST", f"/SefinNacional/nfse/{KEY}/eventos", 201, {"eventoXmlGZipB64": double})
        self.assertEqual(self.sefin.register_event(KEY, b"<pedRegEvento/>"), b"<evento/>")
        sent = json.loads(self.server.requests[-1][3])
        self.assertIn("pedidoRegistroEventoXmlGZipB64", sent)

        self.server.json("POST", f"/SefinNacional/nfse/{KEY}/eventos", 400, {"erro": [{"codigo": "E0840", "descricao": "já cancelada"}]})
        with self.assertRaises(RejectionError) as ctx:
            self.sefin.register_event(KEY, b"<pedRegEvento/>")
        self.assertEqual(ctx.exception.codes, ["E0840"])

    def test_adn_distribution(self):
        self.server.json("GET", "/contribuintes/DFe/0?lote=true&cnpjConsulta=11222333000181", 200, {
            "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
            "LoteDFe": [
                {"NSU": 1, "ChaveAcesso": KEY, "TipoDocumento": "NFSE", "ArquivoXml": encode_xml(NFSE_XML)},
                {"NSU": 2, "ChaveAcesso": KEY, "TipoDocumento": "EVENTO", "TipoEvento": "CANCELAMENTO", "ArquivoXml": encode_xml(b"<e/>")},
            ],
        })
        batch = self.adn.fetch_dfe(0, cnpj="11222333000181")
        self.assertEqual(batch.last_nsu, 2)
        self.assertEqual(batch.documents[0].xml, NFSE_XML)
        self.assertFalse(batch.exhausted)

        self.server.json("GET", "/contribuintes/DFe/2?lote=true", 404, {"StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO", "LoteDFe": []})
        self.assertTrue(self.adn.fetch_dfe(2).exhausted)

        self.server.json("GET", "/contribuintes/DFe/3?lote=true", 400, {"StatusProcessamento": "REJEICAO", "Erros": [{"Codigo": "E2245", "Descricao": "Não há procuração"}]})
        with self.assertRaises(RejectionError) as ctx:
            self.adn.fetch_dfe(3)
        self.assertEqual(ctx.exception.codes, ["E2245"])

        self.server.routes[("GET", "/contribuintes/DFe/4?lote=true")] = (403, b"<html>WAF</html>", {})
        with self.assertRaises(TransportError):
            self.adn.fetch_dfe(4)

    def test_server_requires_client_certificate(self):
        no_client_cert = ssl.create_default_context(cafile=self.ca_file)
        client = SefinClient(no_client_cert, self.sefin.environment, timeout=5)
        with self.assertRaises(TransportError):
            client.dps_exists(DPS_ID)


if __name__ == "__main__":
    unittest.main()
