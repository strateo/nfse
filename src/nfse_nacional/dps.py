"""DPS (Declaração de Prestação de Serviço) model and XML builder.

Field names mirror the official XML tags (Anexo I) so each value can be traced to the
layout spreadsheet; module, class and function names are English. Only the groups needed
for domestic service issuance are modelled; anything else is rejected by the XSD rather
than silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from lxml import etree

from . import __version__
from .documents import dps_id, is_valid_cnpj, is_valid_cpf
from .environment import Environment, Layout

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
DPS_VERSION = "1.01"
BRASILIA = timezone(timedelta(hours=-3))
VER_APLIC = f"nfse_nacional {__version__}"[:20]

# SEFIN rejects dhEmi later than its own processing time (E0008); its clock runs up to
# ~1 s behind, so default timestamps are backdated a few seconds.
DH_EMI_MARGIN = timedelta(seconds=5)

OP_SIMP_NAC_NAO_OPTANTE = "1"
OP_SIMP_NAC_MEI = "2"
OP_SIMP_NAC_ME_EPP = "3"


@dataclass(frozen=True)
class Address:
    """TCEndereco: national (cMun + CEP) or foreign (cPais + cEndPost + xCidade + xEstProvReg)."""

    xLgr: str
    nro: str
    xBairro: str
    cMun: str | None = None
    CEP: str | None = None
    cPais: str | None = None
    cEndPost: str | None = None
    xCidade: str | None = None
    xEstProvReg: str | None = None
    xCpl: str | None = None


@dataclass(frozen=True)
class Provider:
    """prest. With tpEmit=1 SEFIN fills name and address from the CNPJ registry and
    rejects them if sent (E0121/E0128), so they are not modelled."""

    opSimpNac: str
    CNPJ: str | None = None
    CPF: str | None = None
    IM: str | None = None
    fone: str | None = None
    email: str | None = None
    regApTribSN: str | None = None
    regEspTrib: str = "0"


@dataclass(frozen=True)
class Taker:
    """toma (TCInfoPessoa). Exactly one of CNPJ, CPF, NIF or cNaoNIF."""

    xNome: str
    CNPJ: str | None = None
    CPF: str | None = None
    NIF: str | None = None
    cNaoNIF: str | None = None
    IM: str | None = None
    end: Address | None = None
    fone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class Service:
    """serv: place of service (cLocPrestacao or cPaisPrestacao) and cServ."""

    cTribNac: str
    xDescServ: str
    cLocPrestacao: str | None = None
    cPaisPrestacao: str | None = None
    cTribMun: str | None = None
    cNBS: str | None = None
    cIntContrib: str | None = None
    xInfComp: str | None = None


@dataclass(frozen=True)
class MunicipalTax:
    """tribMun. pAliq is a percentage (2.5 means 2.5%), never a fraction."""

    tribISSQN: str = "1"
    tpRetISSQN: str = "1"
    pAliq: Decimal | None = None
    cPaisResult: str | None = None
    tpImunidade: str | None = None


@dataclass(frozen=True)
class FederalTax:
    """tribFed: PIS/COFINS block and federal withholdings."""

    CST: str | None = None
    vBCPisCofins: Decimal | None = None
    pAliqPis: Decimal | None = None
    pAliqCofins: Decimal | None = None
    vPis: Decimal | None = None
    vCofins: Decimal | None = None
    tpRetPisCofins: str | None = None
    vRetCP: Decimal | None = None
    vRetIRRF: Decimal | None = None
    vRetCSLL: Decimal | None = None


@dataclass(frozen=True)
class TotalTax:
    """totTrib choice. ``percent()`` and ``amount()`` are open to every regime. ``mei()``
    is refused for ME/EPP (E0712) and Não Optante (E0713); ``simples()`` is refused for
    MEI (E0710) and Não Optante (E0713)."""

    indTotTrib: str | None = None
    pTotTribSN: Decimal | None = None
    percent_fed_est_mun: tuple[Decimal, Decimal, Decimal] | None = None
    amount_fed_est_mun: tuple[Decimal, Decimal, Decimal] | None = None

    @classmethod
    def mei(cls) -> TotalTax:
        return cls(indTotTrib="0")

    @classmethod
    def simples(cls, pTotTribSN: Decimal) -> TotalTax:
        return cls(pTotTribSN=pTotTribSN)

    @classmethod
    def percent(cls, fed: Decimal, est: Decimal, mun: Decimal) -> TotalTax:
        return cls(percent_fed_est_mun=(fed, est, mun))

    @classmethod
    def amount(cls, fed: Decimal, est: Decimal, mun: Decimal) -> TotalTax:
        return cls(amount_fed_est_mun=(fed, est, mun))


@dataclass(frozen=True)
class IbsCbs:
    """IBSCBS group (tax reform). Requires cNBS on the service (E0322)."""

    cIndOp: str
    CST: str
    cClassTrib: str
    indDest: str = "0"
    finNFSe: str = "0"
    indFinal: str | None = None
    tpOper: str | None = None


@dataclass(frozen=True)
class Substitution:
    """subst: a DPS carrying this replaces chSubstda; SEFIN generates event 105102 itself."""

    chSubstda: str
    cMotivo: str
    xMotivo: str | None = None


@dataclass(frozen=True)
class Dps:
    cLocEmi: str
    serie: str
    nDPS: str
    dCompet: date
    prest: Provider
    serv: Service
    vServ: Decimal
    tribMun: MunicipalTax
    totTrib: TotalTax
    toma: Taker | None = None
    vDescIncond: Decimal | None = None
    vDescCond: Decimal | None = None
    tribFed: FederalTax | None = None
    ibscbs: IbsCbs | None = None
    subst: Substitution | None = None
    dhEmi: datetime | None = None
    verAplic: str = VER_APLIC

    @property
    def id(self) -> str:
        return dps_id(self.cLocEmi, cnpj=self.prest.CNPJ, cpf=self.prest.CPF, serie=self.serie, n_dps=self.nDPS)


def format_decimal(value: Decimal | int | str) -> str:
    """Format a monetary/percentage value as the XSD TSDec*V2 types expect ("1500.00").

    Floats are refused (binary rounding) and so is anything with more than two decimal
    places: silently rounding 0.025 to "0.03" is how a 2.5% rate becomes 0.03%.
    """
    if isinstance(value, float):
        raise TypeError("use Decimal, int or str for amounts, not float")
    d = Decimal(value)
    if not d.is_finite() or d < 0:
        raise ValueError(f"amount must be a non-negative finite number: {value!r}")
    if d.as_tuple().exponent < -2:
        raise ValueError(f"amount has more than 2 decimal places: {value!r}")
    return str(d.quantize(Decimal("0.01")))


def format_datetime(value: datetime) -> str:
    """TSDateTimeUTC: Brasília offset, second precision, no 'Z' ("2026-11-03T10:15:00-03:00")."""
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(BRASILIA).replace(microsecond=0).isoformat()


def now_brasilia() -> datetime:
    return datetime.now(BRASILIA) - DH_EMI_MARGIN


def _sub(parent: etree._Element, tag: str, value: object | None = None) -> etree._Element | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("use Decimal, int or str for amounts, not float")
    el = etree.SubElement(parent, f"{{{NFSE_NS}}}{tag}")
    if isinstance(value, Decimal):
        el.text = format_decimal(value)
    elif value is not True:
        el.text = str(value)
    return el


def _group(parent: etree._Element, tag: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{NFSE_NS}}}{tag}")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate(dps: Dps, dh_emi: datetime) -> None:
    """Offline guards for rules every reference implementation confirmed against SEFIN.

    Run before taking an nDPS from the series: a rejected DPS still burns its number.
    """
    # Anexo I, level 1: this builder emits through the own-application channel.
    _check(re.fullmatch(r"[0-9]{1,5}", dps.serie) is not None and 1 <= int(dps.serie) <= 49999,
           "own-application DPS serie must be between 1 and 49999 (E0010)")
    _check(dh_emi.tzinfo is not None and dh_emi.utcoffset() is not None, "datetime must be timezone-aware")
    _check(dps.dCompet <= dh_emi.astimezone(BRASILIA).date(),
           "dCompet must not be later than the emission date in Brasilia (E0015)")
    p = dps.prest
    if isinstance(dps.vServ, float):
        raise TypeError("use Decimal, int or str for amounts, not float")
    if dps.tribFed is not None:
        f = dps.tribFed
        # Anexo I piscofins requires CST; never discard dependent caller input.
        _check(f.CST is not None or all(getattr(f, field) is None for field in
               ("vBCPisCofins", "pAliqPis", "pAliqCofins", "vPis", "vCofins", "tpRetPisCofins")),
               "PIS/COFINS values require CST")
    _check((p.CNPJ is None) != (p.CPF is None), "prest needs exactly one of CNPJ or CPF")
    _check(p.CNPJ is None or is_valid_cnpj(p.CNPJ), f"invalid prest CNPJ: {p.CNPJ!r}")
    _check(p.CPF is None or is_valid_cpf(p.CPF), f"invalid prest CPF: {p.CPF!r}")
    _check(p.opSimpNac in ("1", "2", "3"), f"opSimpNac must be 1, 2 or 3: {p.opSimpNac!r}")
    _check(
        (p.opSimpNac == OP_SIMP_NAC_ME_EPP) == (p.regApTribSN is not None),
        "regApTribSN is required for ME/EPP and forbidden otherwise (E0166/E0162)",
    )

    t = dps.totTrib
    chosen = [t.indTotTrib, t.pTotTribSN, t.percent_fed_est_mun, t.amount_fed_est_mun]
    _check(sum(v is not None for v in chosen) == 1, "totTrib needs exactly one option")
    if p.opSimpNac == OP_SIMP_NAC_MEI:
        _check(t.pTotTribSN is None, "MEI must not send pTotTribSN (E0710)")
        _check(dps.tribFed is None, "MEI must not send tribFed (E0676)")
        _check(dps.tribMun.pAliq is None, "MEI must not send pAliq (E0600)")
    elif p.opSimpNac == OP_SIMP_NAC_ME_EPP:
        _check(t.indTotTrib is None, "ME/EPP must not send indTotTrib (E0712)")
    else:
        _check(
            t.indTotTrib is None and t.pTotTribSN is None,
            "Não Optante must use TotalTax.percent() or TotalTax.amount() (E0713)",
        )

    if dps.tribMun.pAliq is not None:
        if isinstance(dps.tribMun.pAliq, float):
            raise TypeError("use Decimal, int or str for amounts, not float")
        rate = Decimal(dps.tribMun.pAliq)
        _check(rate.is_finite(), "pAliq must be a finite number")
        _check(rate <= 5, "pAliq above 5% (E0595); it is a percentage, not a fraction")
    if p.opSimpNac == OP_SIMP_NAC_ME_EPP and p.regApTribSN == "1":
        # ceiling: no BM group is modelled; revisit the active-municipality benefit
        # exceptions before adding BM support. Inactive municipalities use E0631/E0628.
        m = dps.tribMun
        if m.tpRetISSQN == "1":
            _check(m.pAliq is None, "omit pAliq for SN-assessed ISS without withholding (E0625/E0631)")
        elif m.tpRetISSQN in ("2", "3"):
            _check(m.tribISSQN not in ("2", "3", "4"),
                   "omit ISS withholding for immunity, service export or non-incidence (E0580)")
            _check(p.regEspTrib == "0", "omit ISS withholding under a special tax regime (E0588)")
            _check(m.pAliq is not None, "pAliq is required for SN-assessed ISS with withholding (E0621/E0628)")
            _check(Decimal(m.pAliq) >= Decimal("1.80"),
                   "withheld SN-assessed ISS needs pAliq of at least 1.80% (E0621/E0628)")
    _check(dps.serv.cTribMun != "000", "omit cTribMun instead of sending '000' (E0315)")
    _check(
        (dps.serv.cLocPrestacao is None) != (dps.serv.cPaisPrestacao is None),
        "serv needs exactly one of cLocPrestacao or cPaisPrestacao",
    )
    _check(dps.ibscbs is None or dps.serv.cNBS is not None, "IBSCBS requires cNBS (E0322)")
    if dps.toma is not None:
        ids = [dps.toma.CNPJ, dps.toma.CPF, dps.toma.NIF, dps.toma.cNaoNIF]
        _check(sum(v is not None for v in ids) == 1, "toma needs exactly one of CNPJ, CPF, NIF or cNaoNIF")
        _check(dps.toma.CNPJ is None or is_valid_cnpj(dps.toma.CNPJ), "invalid toma CNPJ")
        _check(dps.toma.CPF is None or is_valid_cpf(dps.toma.CPF), "invalid toma CPF")
        _check(
            dps.toma.CNPJ is None or dps.toma.CNPJ != p.CNPJ, "toma and prest must differ (E0202)"
        )
        if dps.toma.NIF is not None:
            _check(
                dps.toma.end is not None and dps.toma.end.cMun is None,
                "toma NIF requires a foreign address (E0242)",
            )


def _address(parent: etree._Element, a: Address) -> None:
    end = _group(parent, "end")
    if a.cMun is not None:
        nac = _group(end, "endNac")
        _sub(nac, "cMun", a.cMun)
        _sub(nac, "CEP", a.CEP)
    else:
        ext = _group(end, "endExt")
        _sub(ext, "cPais", a.cPais)
        _sub(ext, "cEndPost", a.cEndPost)
        _sub(ext, "xCidade", a.xCidade)
        _sub(ext, "xEstProvReg", a.xEstProvReg)
    _sub(end, "xLgr", a.xLgr)
    _sub(end, "nro", a.nro)
    _sub(end, "xCpl", a.xCpl)
    _sub(end, "xBairro", a.xBairro)


def _taker(parent: etree._Element, t: Taker) -> None:
    toma = _group(parent, "toma")
    _sub(toma, "CNPJ", t.CNPJ)
    _sub(toma, "CPF", t.CPF)
    _sub(toma, "NIF", t.NIF)
    _sub(toma, "cNaoNIF", t.cNaoNIF)
    _sub(toma, "IM", t.IM)
    _sub(toma, "xNome", t.xNome)
    if t.end is not None:
        _address(toma, t.end)
    _sub(toma, "fone", t.fone)
    _sub(toma, "email", t.email)


def _service(parent: etree._Element, s: Service, layout: Layout) -> None:
    serv = _group(parent, "serv")
    loc = _group(serv, "locPrest")
    _sub(loc, "cLocPrestacao", s.cLocPrestacao)
    _sub(loc, "cPaisPrestacao", s.cPaisPrestacao)
    c_serv = _group(serv, "cServ")
    _sub(c_serv, "cTribNac", s.cTribNac)
    _sub(c_serv, "cTribMun", s.cTribMun)
    if layout is Layout.V1_01:
        _sub(c_serv, "xDescServ", s.xDescServ)
    _sub(c_serv, "cNBS", s.cNBS)
    _sub(c_serv, "cIntContrib", s.cIntContrib)
    if layout is Layout.NT009:
        _sub(c_serv, "xDescServ", s.xDescServ)
    if s.xInfComp is not None:
        _sub(_group(serv, "infoCompl"), "xInfComp", s.xInfComp)


def _values(parent: etree._Element, dps: Dps) -> None:
    valores = _group(parent, "valores")
    _sub(_group(valores, "vServPrest"), "vServ", Decimal(dps.vServ))
    if dps.vDescIncond is not None or dps.vDescCond is not None:
        desc = _group(valores, "vDescCondIncond")
        _sub(desc, "vDescIncond", dps.vDescIncond)
        _sub(desc, "vDescCond", dps.vDescCond)

    trib = _group(valores, "trib")
    m = dps.tribMun
    mun = _group(trib, "tribMun")
    _sub(mun, "tribISSQN", m.tribISSQN)
    _sub(mun, "cPaisResult", m.cPaisResult)
    _sub(mun, "tpImunidade", m.tpImunidade)
    _sub(mun, "tpRetISSQN", m.tpRetISSQN)
    _sub(mun, "pAliq", m.pAliq)

    f = dps.tribFed
    if f is not None:
        fed = _group(trib, "tribFed")
        if f.CST is not None:
            pc = _group(fed, "piscofins")
            _sub(pc, "CST", f.CST)
            _sub(pc, "vBCPisCofins", f.vBCPisCofins)
            _sub(pc, "pAliqPis", f.pAliqPis)
            _sub(pc, "pAliqCofins", f.pAliqCofins)
            _sub(pc, "vPis", f.vPis)
            _sub(pc, "vCofins", f.vCofins)
            _sub(pc, "tpRetPisCofins", f.tpRetPisCofins)
        _sub(fed, "vRetCP", f.vRetCP)
        _sub(fed, "vRetIRRF", f.vRetIRRF)
        _sub(fed, "vRetCSLL", f.vRetCSLL)

    t = dps.totTrib
    tot = _group(trib, "totTrib")
    if t.amount_fed_est_mun is not None:
        v = _group(tot, "vTotTrib")
        for tag, amount in zip(("vTotTribFed", "vTotTribEst", "vTotTribMun"), t.amount_fed_est_mun):
            _sub(v, tag, format_decimal(amount))
    elif t.percent_fed_est_mun is not None:
        p = _group(tot, "pTotTrib")
        for tag, pct in zip(("pTotTribFed", "pTotTribEst", "pTotTribMun"), t.percent_fed_est_mun):
            _sub(p, tag, format_decimal(pct))
    else:
        _sub(tot, "indTotTrib", t.indTotTrib)
        _sub(tot, "pTotTribSN", t.pTotTribSN)


def _ibscbs(parent: etree._Element, g: IbsCbs, layout: Layout) -> None:
    ibs = _group(parent, "IBSCBS")
    if layout is Layout.V1_01:
        _sub(ibs, "finNFSe", g.finNFSe)
    _sub(ibs, "indFinal", g.indFinal)
    _sub(ibs, "cIndOp", g.cIndOp)
    _sub(ibs, "tpOper", g.tpOper)
    _sub(ibs, "indDest", g.indDest)
    trib = _group(_group(ibs, "valores"), "trib")
    if layout is Layout.V1_01:
        trib = _group(trib, "gIBSCBS")
    _sub(trib, "CST", g.CST)
    _sub(trib, "cClassTrib", g.cClassTrib)


def build_dps(dps: Dps, environment: Environment) -> etree._Element:
    """Build the unsigned ``<DPS>`` element in the layout the environment currently expects."""
    dh_emi = dps.dhEmi if dps.dhEmi is not None else now_brasilia()
    _validate(dps, dh_emi)
    layout = environment.layout
    root = etree.Element(f"{{{NFSE_NS}}}DPS", nsmap={None: NFSE_NS}, versao=DPS_VERSION)
    inf = etree.SubElement(root, f"{{{NFSE_NS}}}infDPS", Id=dps.id)
    _sub(inf, "tpAmb", environment.tp_amb)
    _sub(inf, "dhEmi", format_datetime(dh_emi))
    _sub(inf, "verAplic", dps.verAplic)
    _sub(inf, "serie", dps.serie)
    _sub(inf, "nDPS", dps.nDPS)
    _sub(inf, "dCompet", dps.dCompet.isoformat())
    # tpEmit 2/3 (taker/intermediary issuing) is refused by SEFIN in this version (E9996).
    _sub(inf, "tpEmit", "1")
    if layout is Layout.NT009:
        _sub(inf, "finNFSe", dps.ibscbs.finNFSe if dps.ibscbs else "0")
    _sub(inf, "cLocEmi", dps.cLocEmi)
    if dps.subst is not None:
        subst = _group(inf, "subst")
        _sub(subst, "chSubstda", dps.subst.chSubstda)
        _sub(subst, "cMotivo", dps.subst.cMotivo)
        _sub(subst, "xMotivo", dps.subst.xMotivo)

    p = dps.prest
    prest = _group(inf, "prest")
    _sub(prest, "CNPJ", p.CNPJ)
    _sub(prest, "CPF", p.CPF)
    _sub(prest, "IM", p.IM)
    _sub(prest, "fone", p.fone)
    _sub(prest, "email", p.email)
    reg = _group(prest, "regTrib")
    _sub(reg, "opSimpNac", p.opSimpNac)
    _sub(reg, "regApTribSN", p.regApTribSN)
    _sub(reg, "regEspTrib", p.regEspTrib)

    if dps.toma is not None:
        _taker(inf, dps.toma)
    _service(inf, dps.serv, layout)
    _values(inf, dps)
    if dps.ibscbs is not None:
        _ibscbs(inf, dps.ibscbs, layout)
    return root
