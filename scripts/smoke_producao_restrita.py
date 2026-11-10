"""End-to-end smoke test against Produção Restrita (homologação) with a real A1 certificate.

Steps, each reported PASS/FAIL with the SEFIN/ADN E-codes:
  1. load the certificate      4. build, validate (XSD), sign and save the DPS
  2. ADN distribution (mTLS)   5. emit (or recover) and save NFS-e XML + DANFSe PDF
  3. HEAD /dps/{id}            6. query the NFS-e back
                               7. with --cancel: cancel it and save the event + watermarked DANFSe

Only Produção Restrita is reachable from here: notes issued there have no legal validity.
The password comes from NFSE_PFX_PASSWORD or a hidden prompt and is never printed or saved.
Every document is written to --out before it is sent. After interruption, recover the
saved signed DPS with SefinClient.recover(); rerunning this script allocates a new number.

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
from datetime import date
from decimal import Decimal
from pathlib import Path

from nfse_nacional import dps as d
from nfse_nacional import events, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional.client import AdnClient, SefinClient
from nfse_nacional.danfse import Watermark, render_danfse
from nfse_nacional.environment import PRODUCAO_RESTRITA as ENV
from nfse_nacional.errors import NfseError, RejectionError, TransportError

STATE_FILE = "smoke-state.json"


@dataclass
class Report:
    out: Path
    steps: list[dict] = field(default_factory=list)

    def record(self, step: str, ok: bool, detail: str, **extra) -> None:
        self.steps.append({"step": step, "ok": ok, "detail": detail, **extra})
        print(f"[{'PASS' if ok else 'FAIL'}] {step}: {detail}", flush=True)

    def save(self) -> None:
        (self.out / "report.json").write_text(json.dumps(self.steps, indent=2, ensure_ascii=False), encoding="utf-8")


def _error_detail(e: NfseError) -> tuple[str, dict]:
    if isinstance(e, RejectionError):
        return str(e), {"codes": e.codes, "body": e.body}
    if isinstance(e, TransportError):
        return str(e), {"status": e.status, "body": e.body.decode("utf-8", "replace")}
    return str(e), {}


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
    report = Report(out)
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

    try:
        batch = adn.fetch_dfe(0)
        report.record("ADN distribution", True, f"{batch.status}, {len(batch.documents)} document(s)")
    except NfseError as e:
        detail, extra = _error_detail(e)
        report.record("ADN distribution", False, detail, **extra)
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
    report.record("emit", True, f"access key {result.access_key}", alerts=alerts)
    try:
        (out / f"{stem}.danfse.pdf").write_bytes(render_danfse(result.nfse_xml))
        report.record("DANFSe", True, f"{stem}.danfse.pdf")
    except Exception as e:  # report and continue: the note itself was issued
        report.record("DANFSe", False, f"{type(e).__name__}: {e}")
        failures += 1

    try:
        fetched = sefin.get_nfse(result.access_key)
        report.record("GET /nfse", True, f"{len(fetched)} bytes, identical to emit response: {fetched == result.nfse_xml}")
    except NfseError as e:
        detail, extra = _error_detail(e)
        report.record("GET /nfse", False, detail, **extra)
        failures += 1

    if args.cancel:
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
            (out / f"{stem}.danfse.cancelled.pdf").write_bytes(render_danfse(result.nfse_xml, Watermark.CANCELLED))
            report.record("cancel", True, "event 101101 registered")
        except NfseError as e:
            detail, extra = _error_detail(e)
            report.record("cancel", False, detail, **extra)
            failures += 1
        except ValueError as e:
            report.record("cancel", False, str(e))
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
    p.add_argument("--dry-run", action="store_true", help="build, sign and save the DPS without network calls")
    p.add_argument("--out", default="smoke-output", help="directory for documents, report and number state")
    args = p.parse_args(argv)
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
