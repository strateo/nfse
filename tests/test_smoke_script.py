import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from _certs import PFX_PASSWORD, make_pki
from nfse_nacional import schemas, signing
from nfse_nacional.client import DfeBatch, DfeDocument, EmissionResult
from nfse_nacional.errors import Message, RejectionError, TransportError
from lxml import etree

from nfse_nacional.environment import PRODUCAO_RESTRITA

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_producao_restrita.py"
spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke"] = smoke  # dataclasses resolve their module through sys.modules
spec.loader.exec_module(smoke)


class SmokeScriptDryRunTest(unittest.TestCase):
    def test_explicit_number_does_not_rewind_counter(self):
        with tempfile.TemporaryDirectory(prefix="nfse-counter-") as tmp:
            out = Path(tmp)
            self.assertEqual(smoke._next_n_dps(out, "11222333000181", "900", "100"), "100")
            self.assertEqual(smoke._next_n_dps(out, "11222333000181", "900", "5"), "5")
            self.assertEqual(smoke._next_n_dps(out, "11222333000181", "900", None), "101")

    def test_signed_document_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory(prefix="nfse-signed-") as tmp:
            path = Path(tmp) / "request.dps.xml"
            smoke._save_signed(path, b"original")
            smoke._save_signed(path, b"original")
            with self.assertRaisesRegex(ValueError, "recover"):
                smoke._save_signed(path, b"changed")
            self.assertEqual(path.read_bytes(), b"original")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nfse-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "a1.pfx").write_bytes(make_pki().client_pfx)

    def run_script(self, *extra):
        recipient = extra or (
            "--toma-cnpj", "11444777000161", "--toma-street", "Rua Teste",
            "--toma-number", "10", "--toma-district", "Centro",
            "--toma-c-mun", "3550308", "--toma-cep", "01310100",
        )
        args = smoke.parse_args([
            "--pfx", str(self.tmp / "a1.pfx"), "--c-loc-emi", "3550308", "--op-simp-nac", "3",
            "--p-tot-trib-sn", "6.00", "--c-trib-nac", "010101",
            "--dry-run", "--out", str(self.tmp / "out"), *recipient,
        ])
        with mock.patch.dict(os.environ, {"NFSE_PFX_PASSWORD": PFX_PASSWORD.decode()}):
            return smoke.run(args)

    def test_dry_run_builds_signed_valid_dps_and_never_reuses_numbers(self):
        with mock.patch.object(smoke, "SefinClient") as sefin, mock.patch.object(smoke, "AdnClient") as adn:
            self.assertEqual(self.run_script(), 0)
        sefin.assert_not_called()
        adn.assert_not_called()
        self.assertEqual(self.run_script(), 0)
        out = self.tmp / "out"
        files = sorted(p.name for p in out.glob("*.dps.xml"))
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].endswith("000000000000001.dps.xml"))
        self.assertTrue(files[1].endswith("000000000000002.dps.xml"))
        root = etree.fromstring((out / files[1]).read_bytes())
        signing.verify(root)
        schemas.validate(root, PRODUCAO_RESTRITA.layout)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(all(step["ok"] for step in report))
        self.assertIn("11222333000181", report[0]["detail"])  # CNPJ read from the certificate CN
        summary = json.loads((out / "acceptance-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["mode"], "dry_run")
        self.assertEqual(summary["layout_release"], "nt009-r1-20261020")
        checks = {check["step"]: check["outcome"] for check in summary["checks"]}
        self.assertEqual(checks["build DPS"], "passed")
        self.assertEqual(checks["emit"], "not_run")
        self.assertNotIn("11222333000181", json.dumps(summary))

    def test_summary_allowlist_excludes_private_fields_and_untrusted_codes(self):
        report = smoke.Report(self.tmp)
        report.record("emit", False, "private body secret CNPJ 11222333000181", status=422,
                      codes=["E0014", "A001", "secret-CNPJ-11222333000181", 123], body={"xml": "private"})
        report.record("private step 11222333000181", False, "private")
        report.save()
        raw = (self.tmp / "acceptance-summary.json").read_text(encoding="utf-8")
        self.assertNotIn("11222333000181", raw)
        self.assertNotIn("secret", raw)
        self.assertNotIn("private", raw)
        failure = next(check for check in json.loads(raw)["checks"] if check["step"] == "emit")
        self.assertEqual(failure, {"step": "emit", "outcome": "failed", "codes": ["E0014", "A001"], "http_status": 422})

    def test_adn_progression_exhaustion_and_request_bound_are_distinct(self):
        def batch(nsu):
            return DfeBatch("DOCUMENTOS_LOCALIZADOS", [DfeDocument(nsu, None, None, None, None, b"<NFSe/>")], [], {})
        empty = DfeBatch("NENHUM_DOCUMENTO_LOCALIZADO", [], [], {})
        scenarios = (([batch(11), batch(12), empty], 3, True, True),
                     ([empty], 3, False, True), ([batch(11)], 1, True, False))
        for responses, bound, progressed, exhausted in scenarios:
            with self.subTest(progressed=progressed, exhausted=exhausted):
                adn = mock.Mock()
                adn.fetch_dfe.side_effect = responses
                report = smoke.Report(self.tmp)
                self.assertTrue(smoke._check_adn(adn, report, 10, bound))
                self.assertEqual(adn.fetch_dfe.call_count, len(responses))
                self.assertEqual(adn.fetch_dfe.call_args_list[0], mock.call(10))
                if len(responses) == 3:
                    self.assertEqual(adn.fetch_dfe.call_args_list, [mock.call(10), mock.call(11), mock.call(12)])
                records = {step["step"]: step for step in report.steps}
                self.assertEqual(records["ADN progression"]["ok"], progressed)
                self.assertEqual(records["ADN exhaustion"]["ok"], exhausted)
                if not progressed:
                    self.assertEqual(records["ADN progression"]["outcome"], "not_observed")

    def test_adn_rejection_and_stalled_cursor_fail_the_run(self):
        for response in (RejectionError(422, [Message("E0014", "private")], {"private": "body"}),
                         SimpleNamespace(exhausted=False, last_nsu=10)):
            with self.subTest(response=type(response).__name__):
                adn = mock.Mock()
                if isinstance(response, Exception):
                    adn.fetch_dfe.side_effect = response
                else:
                    adn.fetch_dfe.return_value = response
                report = smoke.Report(self.tmp)
                self.assertFalse(smoke._check_adn(adn, report, 10, 2))
                self.assertFalse(report.steps[0]["ok"])
                self.assertEqual(adn.fetch_dfe.call_count, 1)

    def test_queries_compare_fiscal_content_and_refuse_dtds(self):
        smoke._require_same_xml(b'<?xml version="1.0"?><x a="1"/>', b'<x a="1"></x>')
        with self.assertRaisesRegex(ValueError, "differs"):
            smoke._require_same_xml(b'<x a="2"/>', b'<x a="1"/>')
        with self.assertRaisesRegex(ValueError, "DTD"):
            smoke._require_same_xml(b'<!DOCTYPE x [<!ENTITY s SYSTEM "file:///private">]><x>&s;</x>', b'<x/>')
        with self.assertRaisesRegex(ValueError, "canonicalized"):
            smoke._require_same_xml(b'<NFSe xmlns="relative"/>', b'<NFSe/>')

    def acceptance_args(self):
        return smoke.parse_args([
            "--pfx", str(self.tmp / "a1.pfx"), "--c-loc-emi", "3550308", "--op-simp-nac", "3",
            "--p-tot-trib-sn", "6.00", "--c-trib-nac", "010101", "--check-recovery", "--cancel",
            "--out", str(self.tmp / "out"),
        ])

    def run_mocked_acceptance(self, *, recovered=None, fetched=None, queried_event=None, interrupted=False):
        xml = b'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe/></NFSe>'
        event = b'<evento xmlns="http://www.sped.fazenda.gov.br/nfse"><infEvento/></evento>'
        key = "3550308" + "1" + "2" + "11222333000181" + "0" * 27
        result = EmissionResult(key, xml, None, [], {})
        with mock.patch.dict(os.environ, {"NFSE_PFX_PASSWORD": PFX_PASSWORD.decode()}), \
                mock.patch.object(smoke, "SefinClient") as client, mock.patch.object(smoke, "AdnClient") as adn, \
                mock.patch.object(smoke, "render_danfse", return_value=b"test PDF"), contextlib.redirect_stdout(io.StringIO()):
            sefin = client.return_value
            sefin.dps_exists.return_value = False
            sefin.emit.side_effect = TransportError("interrupted") if interrupted else None
            sefin.emit.return_value = result
            sefin.get_nfse.return_value = fetched if fetched is not None else xml
            sefin.recover.return_value = recovered if recovered is not None else result
            sefin.register_event.return_value = event
            sefin.get_event.return_value = queried_event if queried_event is not None else event
            adn.return_value.fetch_dfe.return_value = DfeBatch("NENHUM_DOCUMENTO_LOCALIZADO", [], [], {})
            code = smoke.run(self.acceptance_args())
        return code, sefin

    def test_mocked_acceptance_recovers_persisted_bytes_and_queries_event(self):
        code, sefin = self.run_mocked_acceptance()
        self.assertEqual(code, 0)
        saved = next((self.tmp / "out").glob("*.dps.xml")).read_bytes()
        sefin.emit.assert_called_once_with(saved)
        root = etree.fromstring(saved)
        sefin.recover.assert_called_once_with(saved, root[0].get("Id"))
        sefin.get_event.assert_called_once_with(sefin.emit.return_value.access_key, "101101")
        report = json.loads((self.tmp / "out/report.json").read_text(encoding="utf-8"))
        self.assertTrue(next(step["ok"] for step in report if step["step"] == "GET event"))

    def test_interrupted_emission_reuses_the_same_document(self):
        code, sefin = self.run_mocked_acceptance(interrupted=True)
        self.assertEqual(code, 0)
        saved = next((self.tmp / "out").glob("*.dps.xml")).read_bytes()
        self.assertEqual(sefin.recover.call_count, 2)
        self.assertTrue(all(call.args[0] == saved for call in sefin.recover.call_args_list))

    def test_mismatching_nfse_recovery_and_event_fail_the_probe(self):
        cases = ({"fetched": b"<different/>"}, {"queried_event": b"<different/>"},
                 {"fetched": b'<NFSe xmlns="relative"/>'}, {"queried_event": b'<evento xmlns="relative"/>'},
                 {"recovered": EmissionResult("3550308" + "1" + "2" + "11222333000181" + "0" * 27,
                                              b'<NFSe xmlns="relative"/>', None, [], {})},
                 {"recovered": EmissionResult("different-key", b"<NFSe/>", None, [], {})})
        for changes in cases:
            with self.subTest(changes=list(changes)):
                code, _ = self.run_mocked_acceptance(**changes)
                self.assertEqual(code, 1)
                summary = json.loads((self.tmp / "out/acceptance-summary.json").read_text(encoding="utf-8"))
                failed_step = "GET /nfse" if "fetched" in changes else ("GET event" if "queried_event" in changes else "recover")
                self.assertEqual(next(check["outcome"] for check in summary["checks"] if check["step"] == failed_step), "failed")

    def test_adn_bounds_are_validated_before_running(self):
        for args in (("--adn-last-nsu", "-1"), ("--adn-max-batches", "0"), ("--adn-max-batches", "101")):
            with self.subTest(args=args), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                smoke.parse_args(["--pfx", "unused", "--c-loc-emi", "3550308", "--op-simp-nac", "2",
                                  "--c-trib-nac", "010101", *args])

    def test_dry_run_supports_individual_and_foreign_recipient_branches(self):
        foreign = (
            "--toma-street", "1 Main Street", "--toma-number", "1",
            "--toma-district", "Central", "--toma-c-pais", "GB",
            "--toma-postal", "SW1A 2AA", "--toma-city", "London", "--toma-region", "England",
        )
        branches = (
            ("--toma-cpf", "52998224725"),
            ("--toma-nif", "GB-123456"),
            ("--toma-c-nao-nif", "1"),
            ("--toma-c-nao-nif", "2"),
        )
        for branch in branches:
            args = branch + (foreign if branch[0] in ("--toma-nif", "--toma-c-nao-nif") else ())
            with self.subTest(branch=branch[0], value=branch[1]):
                self.assertEqual(self.run_script(*args), 0)

    def test_foreign_address_rejects_brazilian_address_fields(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            smoke.parse_args([
                "--pfx", "x", "--c-loc-emi", "3550308", "--op-simp-nac", "2",
                "--c-trib-nac", "010101", "--toma-nif", "GB-123456",
                "--toma-street", "1 Main Street", "--toma-number", "1", "--toma-district", "Central",
                "--toma-c-pais", "GB", "--toma-postal", "SW1A 2AA", "--toma-city", "London",
                "--toma-region", "England", "--toma-c-mun", "3550308",
            ])

    def test_address_without_recipient_is_not_silently_discarded(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            smoke.parse_args([
                "--pfx", "x", "--c-loc-emi", "3550308", "--op-simp-nac", "2", "--c-trib-nac", "010101",
                "--toma-street", "Rua Teste", "--toma-number", "1", "--toma-district", "Centro",
                "--toma-c-mun", "3550308", "--toma-cep", "01310100",
            ])

    def test_requires_regime_specific_totals(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            smoke.parse_args(["--pfx", "x", "--c-loc-emi", "3550308", "--op-simp-nac", "3", "--c-trib-nac", "010101"])

    def test_wrong_password_fails_cleanly(self):
        args = smoke.parse_args(["--pfx", str(self.tmp / "a1.pfx"), "--c-loc-emi", "3550308", "--op-simp-nac", "2",
                                 "--c-trib-nac", "010101", "--dry-run", "--out", str(self.tmp / "out")])
        with mock.patch.dict(os.environ, {"NFSE_PFX_PASSWORD": "wrong"}):
            self.assertEqual(smoke.run(args), 1)


if __name__ == "__main__":
    unittest.main()
