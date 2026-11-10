"""End-to-end smoke test against Produção Restrita (homologação) with a real A1 certificate.

Steps, each reported PASS/FAIL with the SEFIN/ADN E-codes:
  1. load the certificate      4. build, validate (XSD), sign and save the DPS
  2. ADN distribution (mTLS)   5. emit (or recover) and save NFS-e XML + DANFSe PDF
  3. HEAD /dps/{id}            6. query the NFS-e back
                               7. with --check-recovery: recover the same persisted DPS
                               8. with --cancel: cancel, query the event and save a watermarked DANFSe

Only Produção Restrita is reachable from here: notes issued there have no legal validity.
The password comes from NFSE_PFX_PASSWORD or a hidden prompt and is never printed or saved.
Every document is written to --out before it is sent. After interruption, recover the
saved signed DPS with SefinClient.recover(); rerunning this script allocates a new number.
Private report.json contains fiscal details. acceptance-summary.json contains only
fixed check names, outcomes, response codes, release identity and timestamps.
ADN pagination is bounded; exhaustion without a document batch does not prove progression.

    python scripts/smoke_producao_restrita.py --pfx empresa.pfx --c-loc-emi 3550308 \\
        --op-simp-nac 3 --p-tot-trib-sn 6.00 --c-trib-nac 010101 --desc "Teste de integração"
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from lxml import etree

from nfse_nacional import __version__
from nfse_nacional import dps as d
from nfse_nacional import events, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional.client import AdnClient, SefinClient
from nfse_nacional.danfse import Watermark, render_danfse
from nfse_nacional.environment import PRODUCAO_RESTRITA as ENV
from nfse_nacional.errors import NfseError, RejectionError, TransportError

STATE_FILE = "smoke-state.json"
ACCEPTANCE_STEPS = (
    "certificate", "build DPS", "HEAD /dps", "emit", "GET /nfse", "recover",
    "cancel", "GET event", "ADN distribution", "ADN progression", "ADN exhaustion",
)


@dataclass
class Report:
    out: Path
    steps: list[dict] = field(default_factory=list)
    dry_run: bool = False
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def record(self, step: str, ok: bool, detail: str, **extra) -> None:
        self.steps.append({"step": step, "ok": ok, "detail": detail, **extra})
        outcome = "NOT OBSERVED" if extra.get("outcome") == "not_observed" else ("PASS" if ok else "FAIL")
        print(f"[{outcome}] {step}: {detail}", flush=True)

    def save(self) -> None:
        (self.out / "report.json").write_text(json.dumps(self.steps, indent=2, ensure_ascii=False), encoding="utf-8")
        # Copy only an allowlist, never descriptions, response bodies, identifiers or XML.
        checks = []
        for name in ACCEPTANCE_STEPS:
            records = [step for step in self.steps if step["step"] == name]
            if not records:
                checks.append({"step": name, "outcome": "not_run"})
            for step in records:
                check = {"step": name, "outcome": "passed" if step["ok"] else "failed"}
                if step.get("outcome") == "not_observed":
                    check["outcome"] = "not_observed"
                check["codes"] = [code for code in step.get("codes", [])
                                  if isinstance(code, str) and re.fullmatch(r"[EA][0-9]{3,4}", code)]
                status = step.get("status")
                if type(status) is int and 100 <= status <= 599:
                    check["http_status"] = status
                checks.append(check)
        summary = {
            "format_version": 1, "package_version": __version__,
            "environment": ENV.name, "layout": ENV.layout.value, "layout_release": ENV.layout.release,
            "mode": "dry_run" if self.dry_run else "live",
            "started_at": self.started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
        }
        (self.out / "acceptance-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _error_detail(e: NfseError) -> tuple[str, dict]:
    if isinstance(e, RejectionError):
        return str(e), {"codes": e.codes, "status": e.status, "body": e.body}
    if isinstance(e, TransportError):
        return str(e), {"status": e.status, "body": e.body.decode("utf-8", "replace")}
    return str(e), {}


def _require_same_xml(actual: bytes, expected: bytes) -> None:
    """Fail a query/recovery check if its fiscal document differs; never echo XML."""
    canonical = []
    for data in (actual, expected):
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            root = etree.fromstring(data, parser)
            if root.getroottree().docinfo.doctype:
                raise ValueError("queried fiscal XML must not contain a DTD")
            canonical.append(etree.tostring(root, method="c14n", exclusive=True, with_comments=False))
        except (etree.XMLSyntaxError, etree.C14NError) as e:
            raise ValueError("queried fiscal XML cannot be parsed or canonicalized") from e
    if canonical[0] != canonical[1]:
        raise ValueError("queried fiscal XML differs from the saved response")


def _check_adn(adn: AdnClient, report: Report, last_nsu: int, max_batches: int) -> bool:
    """Read a bounded set of batches, distinguishing empty queues from progression."""
    progressed = False
    try:
        for _ in range(max_batches):
            batch = adn.fetch_dfe(last_nsu)
            if batch.exhausted:
                report.record("ADN distribution", True, "distribution completed")
                report.record("ADN progression", progressed, "document batch advanced NSU" if progressed else
                              "no document batch observed", outcome="passed" if progressed else "not_observed")
                report.record("ADN exhaustion", True, "empty queue observed")
                return True
            next_nsu = batch.last_nsu
            if next_nsu is None or next_nsu <= last_nsu:
                raise ValueError("ADN batch does not advance NSU")
            # These are acceptance probes, not a durable fiscal-document consumer.
            last_nsu = next_nsu
            progressed = True
    except (NfseError, ValueError) as e:
        detail, extra = _error_detail(e) if isinstance(e, NfseError) else (str(e), {})
        report.record("ADN distribution", False, detail, **extra)
        report.record("ADN progression", progressed, "progress before failure" if progressed else "no progress observed",
                      outcome="passed" if progressed else "not_observed")
        return False
    report.record("ADN distribution", True, "batch limit reached")
    report.record("ADN progression", progressed, "document batch advanced NSU")
    report.record("ADN exhaustion", False, "not observed before batch limit", outcome="not_observed")
    return True


def _cnpj_from_certificate(cert: Certificate) -> str | None:
    # ICP-Brasil e-CNPJ carries the CNPJ in the subject CN as "NAME:CNPJ".
    for attr in cert.cert.subject:
        if attr.oid.dotted_string == "2.5.4.3" and ":" in attr.value:
            candidate = attr.value.rsplit(":", 1)[1].strip()
            if len(candidate) == 14:
                return candidate
    return None


def _next_n_dps(out: Path, cnpj: str, serie: str, explicit: str | None) -> str:
    """Never reuse a number: SEFIN answers a repeated DPS Id with the note it already issued."""
    path = out / STATE_FILE
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = f"{cnpj}/{serie}"
    previous = int(state.get(key, "0"))
    n_dps = explicit if explicit is not None else str(previous + 1)
    if not re.fullmatch(r"[1-9][0-9]{0,14}", n_dps):
        raise ValueError("nDPS must be 1-15 digits without leading zeros")
    state[key] = str(max(previous, int(n_dps)))
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return n_dps


def _save_signed(path: Path, signed: bytes) -> None:
    """Keep the exact persisted request for recovery instead of replacing it."""
    try:
        with path.open("xb") as f:
            f.write(signed)
    except FileExistsError:
        if path.read_bytes() != signed:
            raise ValueError("signed DPS already exists with different bytes; recover the saved document") from None


def build_note(args: argparse.Namespace, cnpj: str, n_dps: str) -> d.Dps:
    toma = None
    if any((args.toma_cnpj, args.toma_cpf, args.toma_nif, args.toma_c_nao_nif)):
        address = None
        if args.toma_street:
            address = d.Address(
                xLgr=args.toma_street,
                nro=args.toma_number,
                xBairro=args.toma_district,
                cMun=args.toma_c_mun,
                CEP=args.toma_cep,
                cPais=args.toma_c_pais,
                cEndPost=args.toma_postal,
                xCidade=args.toma_city,
                xEstProvReg=args.toma_region,
                xCpl=args.toma_complement,
            )
        toma = d.Taker(
            xNome=args.toma_nome,
            CNPJ=args.toma_cnpj,
            CPF=args.toma_cpf,
            NIF=args.toma_nif,
            cNaoNIF=args.toma_c_nao_nif,
            end=address,
        )
    if args.op_simp_nac == d.OP_SIMP_NAC_MEI:
        tot = d.TotalTax.mei()
    elif args.op_simp_nac == d.OP_SIMP_NAC_ME_EPP:
        tot = d.TotalTax.simples(Decimal(args.p_tot_trib_sn))
    else:
        tot = d.TotalTax.percent(*(Decimal(v) for v in args.p_tot_trib.split(",")))
    return d.Dps(
        cLocEmi=args.c_loc_emi,
        serie=args.serie,
        nDPS=n_dps,
        dCompet=d.now_brasilia().date(),
        prest=d.Provider(
            opSimpNac=args.op_simp_nac,
            CNPJ=cnpj,
            IM=args.im,
            regApTribSN=args.reg_ap_trib_sn if args.op_simp_nac == d.OP_SIMP_NAC_ME_EPP else None,
        ),
        serv=d.Service(
            cTribNac=args.c_trib_nac,
            cTribMun=args.c_trib_mun,
            cNBS=args.c_nbs,
            xDescServ=args.desc,
            cLocPrestacao=args.c_loc_emi,
        ),
        vServ=Decimal(args.v_serv),
        tribMun=d.MunicipalTax(pAliq=Decimal(args.p_aliq) if args.p_aliq else None),
        totTrib=tot,
        toma=toma,
    )


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = Report(out, dry_run=args.dry_run)
    profile = signing.SignatureProfile[args.profile]

    password = os.environ.get("NFSE_PFX_PASSWORD")
    if password is None:
        password = getpass.getpass("PFX password (hidden): ")
    try:
        cert = Certificate.from_pfx_file(args.pfx, password)
    except (ValueError, OSError) as e:
        report.record("certificate", False, f"cannot load PFX: {e}")
        report.save()
        return 1
    cnpj = args.cnpj or _cnpj_from_certificate(cert)
    if cert.is_expired():
        report.record("certificate", False, f"expired on {cert.not_valid_after:%Y-%m-%d}")
        report.save()
        return 1
    if not cnpj:
        report.record("certificate", False, "CNPJ not found in the certificate subject; pass --cnpj")
        report.save()
        return 1
    report.record("certificate", True, f"CNPJ {cnpj}, valid until {cert.not_valid_after:%Y-%m-%d}")

    try:
        n_dps = _next_n_dps(out, cnpj, args.serie, args.n_dps)
        note = build_note(args, cnpj, n_dps)
        stem = note.id
        root = d.build_dps(note, ENV)
        signing.sign(root, cert, profile)
        schemas.validate(root, ENV.layout)
        signing.verify(root)
    except ValueError as e:
        report.record("build DPS", False, str(e))
        report.save()
        return 1
    signed = signing.to_bytes(root)
    try:
        _save_signed(out / f"{stem}.dps.xml", signed)
    except (ValueError, OSError) as e:
        report.record("save DPS", False, str(e))
        report.save()
        return 1
    report.record("build DPS", True, f"{note.id} (nDPS {n_dps}, {profile.name}) saved, XSD valid, signature verified")

    if args.dry_run:
        report.record("dry run", True, "stopped before any network call")
        report.save()
        return 0

    ctx = cert.ssl_context()
    sefin, adn = SefinClient(ctx, ENV), AdnClient(ctx, ENV)
    failures = 0

    if not _check_adn(adn, report, args.adn_last_nsu, args.adn_max_batches):
        failures += 1

    try:
        exists = sefin.dps_exists(note.id)
        report.record("HEAD /dps", True, f"exists={exists}")
    except NfseError as e:
        detail, extra = _error_detail(e)
        report.record("HEAD /dps", False, detail, **extra)
        failures += 1

    try:
        try:
            result = sefin.emit(signed)
        except TransportError as e:
            report.record("emit", False, f"{e}; trying recover()")
            result = sefin.recover(signed, note.id)
    except NfseError as e:
        detail, extra = _error_detail(e)
        report.record("emit", False, detail, **extra)
        report.save()
        return 1
    (out / f"{stem}.nfse.xml").write_bytes(result.nfse_xml)
    alerts = [f"[{a.code}] {a.description}" for a in result.alerts]
    report.record("emit", True, f"access key {result.access_key}", alerts=alerts,
                  codes=[alert.code for alert in result.alerts])
    try:
        (out / f"{stem}.danfse.pdf").write_bytes(render_danfse(result.nfse_xml))
        report.record("DANFSe", True, f"{stem}.danfse.pdf")
    except Exception as e:  # report and continue: the note itself was issued
        report.record("DANFSe", False, f"{type(e).__name__}: {e}")
        failures += 1

    try:
        fetched = sefin.get_nfse(result.access_key)
        _require_same_xml(fetched, result.nfse_xml)
        report.record("GET /nfse", True, "fiscal XML matches the saved emission response")
    except (NfseError, ValueError, etree.XMLSyntaxError) as e:
        detail, extra = _error_detail(e) if isinstance(e, NfseError) else ("query XML did not match", {})
        report.record("GET /nfse", False, detail, **extra)
        failures += 1

    if args.check_recovery:
        try:
            recovered = sefin.recover((out / f"{stem}.dps.xml").read_bytes(), note.id)
            if recovered.access_key != result.access_key:
                raise ValueError("recovery returned a different access key")
            _require_same_xml(recovered.nfse_xml, result.nfse_xml)
            report.record("recover", True, "same persisted DPS resolves to the saved NFS-e")
        except (NfseError, ValueError, etree.XMLSyntaxError) as e:
            detail, extra = _error_detail(e) if isinstance(e, NfseError) else ("recovery did not match the saved response", {})
            report.record("recover", False, detail, **extra)
            failures += 1

    if args.cancel:
        cancel_step = "cancel"
        try:
            event = events.build_cancellation(
                result.access_key, events.CANCEL_REASON_ISSUE_ERROR,
                "Nota emitida em teste de integração", ENV, author_cnpj=cnpj,
            )
            signing.sign(event, cert, profile)
            schemas.validate(event, ENV.layout)
            signed_event = signing.to_bytes(event)
            (out / f"{stem}.cancel.request.xml").write_bytes(signed_event)
            event_xml = sefin.register_event(result.access_key, signed_event)
            (out / f"{stem}.cancel.event.xml").write_bytes(event_xml)
            report.record("cancel", True, "event 101101 registered")
            cancel_step = "GET event"
            queried_event = sefin.get_event(result.access_key, events.CANCELLATION)
            _require_same_xml(queried_event, event_xml)
            report.record("GET event", True, "queried event matches the registration response")
            cancel_step = "cancelled DANFSe"
            (out / f"{stem}.danfse.cancelled.pdf").write_bytes(render_danfse(result.nfse_xml, Watermark.CANCELLED))
        except NfseError as e:
            detail, extra = _error_detail(e)
            report.record(cancel_step, False, detail, **extra)
            failures += 1
        except (ValueError, etree.XMLSyntaxError):
            report.record(cancel_step, False, "cancellation/query validation failed")
            failures += 1

    report.save()
    print(f"\nArtifacts and report.json in {out.resolve()}")
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pfx", required=True, help="A1 certificate (.pfx/.p12) of the issuing CNPJ")
    p.add_argument("--cnpj", help="issuer CNPJ when it cannot be read from the certificate")
    p.add_argument("--c-loc-emi", required=True, help="IBGE code of the issuer's município (7 digits)")
    p.add_argument("--op-simp-nac", required=True, choices=["1", "2", "3"], help="1 Não Optante, 2 MEI, 3 ME/EPP")
    p.add_argument("--reg-ap-trib-sn", default="1", choices=["1", "2", "3"], help="ME/EPP only")
    p.add_argument("--p-tot-trib-sn", help="ME/EPP approximate tax %% (e.g. 6.00)")
    p.add_argument("--p-tot-trib", help="Não Optante approximate tax %% as FED,EST,MUN")
    p.add_argument("--im", help="Inscrição Municipal, only if the CNC requires it (E0116/E0120)")
    p.add_argument("--c-trib-nac", required=True, help="national service code, 6 digits (e.g. 010101)")
    p.add_argument("--c-trib-mun", help="municipal sub-code (3 digits), only where the município uses it")
    p.add_argument("--c-nbs", help="NBS code (9 digits)")
    p.add_argument("--desc", default="Serviço de teste de integração - sem valor fiscal", help="xDescServ")
    p.add_argument("--v-serv", default="1.00", help="service value (default 1.00)")
    p.add_argument("--p-aliq", help="ISS rate %% (omit for ME/EPP regApTribSN=1 without withholding: E0625/E0631; other regimes depend on municipal rules)")
    p.add_argument("--toma-cnpj", help="Brazilian company recipient CNPJ")
    p.add_argument("--toma-cpf", help="Brazilian individual recipient CPF")
    p.add_argument("--toma-nif", help="foreign recipient NIF")
    p.add_argument("--toma-c-nao-nif", choices=["1", "2"], help="foreign recipient NIF absence reason")
    p.add_argument("--toma-nome", default="Tomador de Teste")
    p.add_argument("--toma-street", help="recipient address street")
    p.add_argument("--toma-number", help="recipient address number")
    p.add_argument("--toma-complement", help="recipient address complement")
    p.add_argument("--toma-district", help="recipient address district")
    p.add_argument("--toma-c-mun", help="recipient Brazilian IBGE municipality code")
    p.add_argument("--toma-cep", help="recipient Brazilian CEP")
    p.add_argument("--toma-c-pais", help="recipient foreign ISO 3166-1 alpha-2 country")
    p.add_argument("--toma-postal", help="recipient foreign postal code")
    p.add_argument("--toma-city", help="recipient foreign city")
    p.add_argument("--toma-region", help="recipient foreign state/province/region")
    p.add_argument("--serie", default="900", help="dedicated DPS series for this system (default 900)")
    p.add_argument("--n-dps", help="force a DPS number (default: next from the state file)")
    p.add_argument("--profile", default="RSA_SHA1", choices=[x.name for x in signing.SignatureProfile])
    p.add_argument("--cancel", action="store_true", help="cancel the note after issuing it")
    p.add_argument("--check-recovery", action="store_true", help="recover the same saved DPS after emission and compare its NFS-e")
    p.add_argument("--adn-last-nsu", type=int, default=0, help="initial ADN cursor (default 0)")
    p.add_argument("--adn-max-batches", type=int, default=10, help="bounded ADN requests; no retry waits (default 10)")
    p.add_argument("--dry-run", action="store_true", help="build, sign and save the DPS without network calls")
    p.add_argument("--out", default="smoke-output", help="directory for documents, report and number state")
    args = p.parse_args(argv)
    if args.adn_last_nsu < 0 or not 1 <= args.adn_max_batches <= 100:
        p.error("--adn-last-nsu must be nonnegative and --adn-max-batches must be between 1 and 100")
    if args.op_simp_nac == "3" and not args.p_tot_trib_sn:
        p.error("--p-tot-trib-sn is required for ME/EPP (E0712)")
    if args.op_simp_nac == "1" and not args.p_tot_trib:
        p.error("--p-tot-trib FED,EST,MUN is required for Não Optante")
    identifiers = [args.toma_cnpj, args.toma_cpf, args.toma_nif, args.toma_c_nao_nif]
    if sum(value is not None for value in identifiers) > 1:
        p.error("pass only one recipient identifier option")
    foreign = bool(args.toma_nif or args.toma_c_nao_nif)
    address_values = [
        args.toma_street, args.toma_number, args.toma_complement, args.toma_district,
        args.toma_c_mun, args.toma_cep, args.toma_c_pais, args.toma_postal,
        args.toma_city, args.toma_region,
    ]
    if any(address_values):
        if not any(identifiers):
            p.error("recipient address requires a recipient identifier")
        required = (
            [args.toma_street, args.toma_number, args.toma_district,
             args.toma_c_pais, args.toma_postal, args.toma_city, args.toma_region]
            if foreign else
            [args.toma_street, args.toma_number, args.toma_district,
             args.toma_c_mun, args.toma_cep]
        )
        if not all(required):
            p.error("recipient address is incomplete for the selected national or foreign branch")
        if foreign and (args.toma_c_mun or args.toma_cep):
            p.error("foreign recipient address cannot include Brazilian municipality or CEP")
        if not foreign and (args.toma_c_pais or args.toma_postal or args.toma_city or args.toma_region):
            p.error("Brazilian recipient address cannot include foreign address fields")
    elif args.toma_cnpj or foreign:
        p.error("a full recipient address is required for CNPJ, NIF, or no-NIF recipients")
    if foreign and not args.toma_c_pais:
        p.error("--toma-c-pais is required for foreign recipients")
    return args


if __name__ == "__main__":
    sys.exit(run(parse_args()))
