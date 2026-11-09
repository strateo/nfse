import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _certs import PFX_PASSWORD, make_pki
from nfse_nacional import schemas, signing
from lxml import etree

from nfse_nacional.environment import PRODUCAO_RESTRITA

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_producao_restrita.py"
spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke"] = smoke  # dataclasses resolve their module through sys.modules
spec.loader.exec_module(smoke)


class SmokeScriptDryRunTest(unittest.TestCase):
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
        self.assertEqual(self.run_script(), 0)
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
