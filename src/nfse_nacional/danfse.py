"""DANFSe v2.0 rendered locally from an authorized NFS-e XML (NT SE/CGNFS-e 008/2026 v1.02).

The ADN DANFSe API was suspended on 2026-08-03; issuers print the document themselves.
Layout follows Anexo I and the field table of NT 008 item 2.4.5, as a single A4 page.
Deviation: NT 008 names Arial (labels) and Microsoft Sans Serif (content), which cannot be
redistributed; the PDF core font Helvetica (metric-compatible with Arial) is used instead.

Cancellation and substitution are events, not part of the NFS-e XML, so the caller passes
the matching ``Watermark`` after checking the note's events.
"""

from __future__ import annotations

import enum
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources

from lxml import etree
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .dps import NFSE_NS
from .pdf_fonts import DEFAULT_FONT_SET, FontSet, activate, current_font_set, deactivate

CONSULTA_URL = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="


class Watermark(enum.Enum):
    CANCELLED = "CANCELADA"
    SUBSTITUTED = "SUBSTITUÍDA"


# Descriptions printed instead of codes ("Utilizar a descrição destas opções"), from the XSD.
TP_AMB = {"1": "Produção", "2": "Homologação"}
AMB_GER = {"1": "Prefeitura", "2": "Sistema Nacional da NFS-e"}
C_STAT = {"100": "NFS-e Gerada", "102": "NFS-e de Decisão Judicial", "103": "NFS-e Avulsa", "107": "NFS-e MEI"}
TP_EMIT = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}
FIN_NFSE = {"0": "NFS-e regular", "1": "NFS-e de crédito", "2": "NFS-e de débito"}
OP_SIMP_NAC = {
    "1": "Não Optante",
    "2": "Optante - Microempreendedor Individual (MEI)",
    "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)",
}
REG_AP_TRIB_SN = {
    "1": "Regime de apuração dos tributos federais e municipal pelo SN",
    "2": "Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN conforme respectiva legislação municipal do tributo",
    "3": "Regime de apuração dos tributos federais e municipal por fora do SN conforme respectivas legislações federal e municipal de cada tributo",
}
TRIB_ISSQN = {"1": "Operação Tributável", "2": "Imunidade", "3": "Exportação de serviço", "4": "Não Incidência"}
REG_ESP_TRIB = {
    "0": "Nenhum", "1": "Ato Cooperado (Cooperativa)", "2": "Estimativa", "3": "Microempresa Municipal",
    "4": "Notário ou Registrador", "5": "Profissional Autônomo", "6": "Sociedade de Profissionais", "9": "Outros",
}
TP_IMUNIDADE = {
    "0": "Imunidade (tipo não informado na nota de origem)",
    "1": "Patrimônio, renda ou serviços, uns dos outros (CF88, Art 150, VI, a)",
    "2": "Templos de qualquer culto (CF88, Art 150, VI, b)",
    "3": "Patrimônio, renda ou serviços dos partidos políticos, entidades sindicais, educação e assistência social (CF88, Art 150, VI, c)",
    "4": "Livros, jornais, periódicos e o papel destinado a sua impressão (CF88, Art 150, VI, d)",
    "5": "Fonogramas e videofonogramas musicais produzidos no Brasil (CF88, Art 150, VI, e)",
}
TP_SUSP = {"1": "Exigibilidade Suspensa por Decisão Judicial", "2": "Exigibilidade Suspensa por Processo Administrativo"}
TP_BM = {"1": "Isenção", "2": "Redução da BC em percentual", "3": "Redução da BC em valor", "4": "Alíquota Diferenciada"}
TP_RET_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
TP_RET_PIS_COFINS = {
    "0": "PIS/COFINS/CSLL Não Retidos", "1": "PIS/COFINS Retidos", "2": "PIS/COFINS Não Retidos",
    "3": "PIS/COFINS/CSLL Retidos", "4": "PIS/COFINS Retidos, CSLL Não Retido", "5": "PIS Retido, COFINS/CSLL Não Retido",
    "6": "COFINS Retido, PIS/CSLL Não Retido", "7": "PIS Não Retido, COFINS/CSLL Retidos",
    "8": "PIS/COFINS Não Retidos, CSLL Retido", "9": "COFINS Não Retido, PIS/CSLL Retidos",
}

NS = {"n": NFSE_NS}
EMPTY = "-"  # NT 008 nota 12: fields without data in the XML print a dash

# Geometry in cm, from Anexo I / item 2.4.5 (positions measured from the top-left corner).
PAGE_H = 29.7
BORDER = 0.2  # item 2.2.2: 0.15-0.20 cm margin
X = (0.30, 5.41, 10.51, 15.62)
COL, WIDE, FULL = 5.09, 10.19, 20.40
ROW = 0.645
STUB_TOP = 28.10
REGULAR, BOLD = "Helvetica", "Helvetica-Bold"
SHADE = 0.95  # 5% grey (item 2.2.3)
WATERMARK_GREY = 0.65  # K35 (item 2.5.1)


@cache
def _municipios() -> dict[str, tuple[str, str]]:
    text = (resources.files("nfse_nacional") / "data" / "municipios.tsv").read_text(encoding="utf-8")
    return {code: (name, uf) for code, name, uf in (line.split("\t") for line in text.splitlines())}


@cache
def _paises() -> dict[str, str]:
    text = (resources.files("nfse_nacional") / "data" / "paises.tsv").read_text(encoding="utf-8")
    return dict(line.split("\t") for line in text.splitlines())


def consulta_url(access_key: str) -> str:
    """URL encoded in the DANFSe QR code (NT 008 item 2.4.3)."""
    return CONSULTA_URL + access_key


# ---------------------------------------------------------------- formatting


def _t(el: etree._Element | None, path: str) -> str | None:
    if el is None:
        return None
    value = el.findtext(path, namespaces=NS)
    return value.strip() if value and value.strip() else None


def _dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def money(value: Decimal | str | None) -> str:
    d = _dec(value) if not isinstance(value, Decimal) else value
    if d is None:
        return EMPTY
    us = f"{d:,.2f}"
    return "R$ " + us.replace(",", "_").replace(".", ",").replace("_", ".")


def percent(value: str | None) -> str:
    d = _dec(value)
    return EMPTY if d is None else f"{d:.2f}".replace(".", ",") + "%"


def document(value: str | None) -> str:
    if not value:
        return EMPTY
    if len(value) == 14:
        return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"
    if len(value) == 11 and value.isdigit():
        return f"{value[:3]}.{value[3:6]}.{value[6:9]}-{value[9:]}"
    return value


def date_br(value: str | None) -> str:
    if not value:
        return EMPTY
    return f"{value[8:10]}/{value[5:7]}/{value[:4]}"


def datetime_br(value: str | None) -> str:
    if not value:
        return EMPTY
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return value


def _cep(value: str | None) -> str | None:
    return f"{value[:2]}.{value[2:5]}-{value[5:]}" if value and len(value) == 8 else value


def _join(*parts: str | None, sep: str = " / ") -> str:
    present = [p for p in parts if p]
    return sep.join(present) if present else EMPTY


def _mun_uf(code: str | None) -> str | None:
    if not code:
        return None
    found = _municipios().get(code)
    return f"{found[0]} / {found[1]}" if found else code


def _uf(code: str | None) -> str | None:
    found = _municipios().get(code or "")
    return found[1] if found else None


def _desc(table: dict[str, str], code: str | None) -> str | None:
    return table.get(code, code) if code else None


def _fit(text: str, font: str, size: float, width_cm: float) -> str:
    limit = width_cm * cm
    font_set = current_font_set()
    measure = (lambda value: font_set.width(value, font, size)) if font_set else (lambda value: stringWidth(value, font, size))
    if measure(text) <= limit:
        return text
    while text and measure(text + "...") > limit:
        text = text[:-1]
    return text.rstrip() + "..."


# ---------------------------------------------------------------- data extraction


class _Nfse:
    """Accessors over the NFS-e XML, tolerant of both DPS layouts (v1.01 and NT 009)."""

    def __init__(self, root: etree._Element):
        if etree.QName(root).namespace != NFSE_NS or etree.QName(root).localname != "NFSe":
            raise ValueError("expected an <NFSe> document in the http://www.sped.fazenda.gov.br/nfse namespace")
        self.inf = root.find("n:infNFSe", NS)
        self.dps = self.inf.find("n:DPS/n:infDPS", NS)
        if self.dps is None:
            raise ValueError("NFS-e has no DPS/infDPS")
        self.emit = self.inf.find("n:emit", NS)
        self.valores = self.inf.find("n:valores", NS)
        self.ibscbs = self.inf.find("n:IBSCBS", NS)
        self.dps_valores = self.dps.find("n:valores", NS)
        self.dps_ibscbs = self.dps.find("n:IBSCBS", NS)

    @property
    def access_key(self) -> str:
        return (self.inf.get("Id") or "").removeprefix("NFS")

    def dps_text(self, path: str) -> str | None:
        return _t(self.dps, path)

    def nfse_text(self, path: str) -> str | None:
        return _t(self.inf, path)

    @property
    def fin_nfse(self) -> str | None:
        return self.dps_text("n:finNFSe") or self.dps_text("n:IBSCBS/n:finNFSe")

    @property
    def ibs_trib(self) -> etree._Element | None:
        if self.dps_ibscbs is None:
            return None
        trib = self.dps_ibscbs.find("n:valores/n:trib", NS)
        nested = trib.find("n:gIBSCBS", NS) if trib is not None else None
        return nested if nested is not None and nested.find("n:CST", NS) is not None else trib


def _party(el: etree._Element | None, fallback: etree._Element | None = None) -> dict[str, str] | None:
    """Fields of a prest/toma/interm/dest group; ``fallback`` is NFS-e ``emit`` for the prestador."""
    if el is None and fallback is None:
        return None

    def get(path: str) -> str | None:
        return _t(el, path) or _t(fallback, path)

    nat_mun = get("n:end/n:endNac/n:cMun") or _t(fallback, "n:enderNac/n:cMun")
    nat_cep = get("n:end/n:endNac/n:CEP") or _t(fallback, "n:enderNac/n:CEP")
    ext = el.find("n:end/n:endExt", NS) if el is not None else None
    if nat_mun:
        mun_uf = _mun_uf(nat_mun)
        ibge_cep = _join(nat_mun, _cep(nat_cep))
    elif ext is not None:
        country = _t(ext, "n:cPais")
        mun_uf = _join(_t(ext, "n:xCidade"), _t(ext, "n:xEstProvReg"), _paises().get(country or "", country))
        ibge_cep = _join(_t(ext, "n:cEndPost"))
    else:
        mun_uf = ibge_cep = EMPTY
    street = el.find("n:end", NS) if el is not None and el.find("n:end", NS) is not None else (
        fallback.find("n:enderNac", NS) if fallback is not None else None
    )
    address = _join(*(_t(street, f"n:{tag}") for tag in ("xLgr", "nro", "xCpl", "xBairro")), sep=", ")
    return {
        "doc": document(get("n:CNPJ") or get("n:CPF") or get("n:NIF")),
        "im": get("n:IM") or EMPTY,
        "fone": get("n:fone") or EMPTY,
        "nome": get("n:xNome") or EMPTY,
        "mun_uf": mun_uf or EMPTY,
        "ibge_cep": ibge_cep,
        "end": address,
        "email": get("n:email") or EMPTY,
    }


# ---------------------------------------------------------------- drawing


class _Page:
    def __init__(self, canvas: Canvas):
        self.c = canvas
        self.y = 0.0  # top of the next block, cm from the top edge

    @staticmethod
    def _py(top_cm: float) -> float:
        return (PAGE_H - top_cm) * cm

    def text(self, x: float, baseline: float, s: str, font: str = REGULAR, size: float = 7, center: bool = False) -> None:
        font_set = current_font_set()
        if font_set is None:
            self.c.setFont(font, size)
            if center:
                self.c.drawCentredString(x * cm, self._py(baseline), s)
            else:
                self.c.drawString(x * cm, self._py(baseline), s)
            return
        runs = font_set.runs(s, font)
        total = font_set.width(s, font, size)
        px = x * cm - total / 2 if center else x * cm
        py = self._py(baseline)
        for run_font, chunk in runs:
            self.c.setFont(run_font, size)
            self.c.drawString(px, py, chunk)
            px += stringWidth(chunk, run_font, size)

    def shade(self, x: float, top: float, w: float, h: float) -> None:
        self.c.saveState()
        self.c.setFillGray(SHADE)
        self.c.rect(x * cm, self._py(top + h), w * cm, h * cm, stroke=0, fill=1)
        self.c.restoreState()

    def hline(self, top: float) -> None:
        self.c.setLineWidth(0.5)
        self.c.line(X[0] * cm, self._py(top), (X[0] + FULL) * cm, self._py(top))

    def field(self, x: float, top: float, w: float, label: str, value: str | None, label_size: float = 6) -> None:
        self.text(x + 0.05, top + 0.24, label, BOLD, label_size)
        self.text(x + 0.05, top + 0.52, _fit(value or EMPTY, REGULAR, 7, w - 0.15))

    def block(self, title: str, rows: list[list[tuple[float, float, str, str | None]]]) -> None:
        """Rows of (x, width, label, value). The shaded title takes column 0 of the first row,
        so the first row starts at column 1; later rows may use all four columns."""
        self.hline(self.y)
        self.shade(X[0], self.y, COL, ROW)
        self.text(X[0] + 0.05, self.y + 0.34, title, BOLD, 7)
        for row in rows:
            for x, w, label, value in row:
                self.field(x, self.y, w, label, value)
            self.y += ROW

    def notice(self, message: str) -> None:
        """Suppressed block (NT 008 notas 2-4): a single line with the official message."""
        self.hline(self.y)
        self.text(X[0] + 0.05, self.y + 0.32, message, BOLD, 7)
        self.y += 0.45

    def wrapped(self, top: float, height: float, lines: list[str], size: float = 7, leading: float = 0.30) -> None:
        capacity = max(int((height - 0.05) // leading), 0)
        if len(lines) > capacity:
            lines = lines[:capacity]
            if lines:
                lines[-1] = _fit(lines[-1] + " ...", REGULAR, size, FULL - 0.1)
        for i, line in enumerate(lines):
            self.text(X[0] + 0.05, top + 0.25 + i * leading, line, REGULAR, size)


def _split(text: str, width: float = FULL - 0.15, size: float = 7) -> list[str]:
    lines: list[str] = []
    font_set = current_font_set()
    if font_set is None:
        from reportlab.lib.utils import simpleSplit
        for paragraph in text.splitlines() or [""]:
            lines.extend(simpleSplit(paragraph, REGULAR, size, width * cm) or [""])
        return lines
    limit = width * cm
    measure = lambda value: font_set.width(value, REGULAR, size)
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}" if line else word
            if line and measure(candidate) > limit:
                lines.append(line)
                line = word
            else:
                line = candidate
            while measure(line) > limit and len(line) > 1:
                # Prefix advances are monotone; avoid rescanning every oversized prefix.
                low, high, cut = 1, len(line) - 1, 1
                while low <= high:
                    middle = (low + high) // 2
                    if measure(line[:middle] + "-") <= limit:
                        cut = middle
                        low = middle + 1
                    else:
                        high = middle - 1
                lines.append(line[:cut] + "-")
                line = line[cut:]
        lines.append(line)
    return lines


def _header(p: _Page, n: _Nfse) -> None:
    p.shade(X[0], 0.30, FULL, 1.16)
    logo = ImageReader(io.BytesIO((resources.files("nfse_nacional") / "data" / "nfse_logo.png").read_bytes()))
    lw, lh = logo.getSize()
    w = 4.0
    h = min(1.0, w * lh / lw)
    p.c.drawImage(logo, X[0] * cm + 0.05 * cm, p._py(0.30 + (1.16 + h) / 2), w * cm, h * cm, mask="auto", preserveAspectRatio=True)
    center = (X[1] + X[3]) / 2
    p.text(center, 0.72, "DANFSe v2.0", BOLD, 9, center=True)
    p.text(center, 1.06, "Documento Auxiliar da NFS-e", BOLD, 9, center=True)
    if n.dps_text("n:tpAmb") == "2":
        p.c.saveState()
        p.c.setFillColorCMYK(0, 1, 1, 0)
        p.text(center, 1.40, "NFS-e SEM VALIDADE JURÍDICA", BOLD, 9, center=True)
        p.c.restoreState()
    uf = _t(n.emit, "n:enderNac/n:UF")
    p.text(X[3], 0.62, _fit(f"Município: {_join(n.nfse_text('n:xLocEmi'), uf, sep=' - ')}", REGULAR, 8, COL), REGULAR, 8)
    p.text(X[3], 0.95, f"Ambiente Gerador: {_desc(AMB_GER, n.nfse_text('n:ambGer')) or EMPTY}", REGULAR, 6)
    p.text(X[3], 1.20, f"Tipo de Ambiente: {_desc(TP_AMB, n.dps_text('n:tpAmb')) or EMPTY}", REGULAR, 6)
    p.y = 1.48


def _identification(p: _Page, n: _Nfse) -> None:
    top = p.y
    p.hline(top)
    p.field(X[0], top, 15.30, "CHAVE DE ACESSO DA NFS-E", n.access_key, label_size=7)
    rows = [
        [("NÚMERO DA NFS-E", n.nfse_text("n:nNFSe")), ("COMPETÊNCIA DA NFS-E", date_br(n.dps_text("n:dCompet"))),
         ("DATA E HORA DA EMISSÃO DA NFS-E", datetime_br(n.nfse_text("n:dhProc")))],
        [("NÚMERO DA DPS", n.dps_text("n:nDPS")), ("SÉRIE DA DPS", n.dps_text("n:serie")),
         ("DATA E HORA DA EMISSÃO DA DPS", datetime_br(n.dps_text("n:dhEmi")))],
        [("EMITENTE DA NFS-E", _desc(TP_EMIT, n.dps_text("n:tpEmit"))), ("SITUAÇÃO DA NFS-E", _desc(C_STAT, n.nfse_text("n:cStat"))),
         ("FINALIDADE", _desc(FIN_NFSE, n.fin_nfse))],
    ]
    row_top = top + 0.79
    for i, row in enumerate(rows):
        if i == 2:
            p.shade(X[0], row_top, COL, 0.67)
        for (label, value), x in zip(row, X):
            p.field(x, row_top, COL, label, value, label_size=7)
        row_top += 0.69

    size = 1.52
    widget = QrCodeWidget(consulta_url(n.access_key), barBorder=0)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size * cm, size * cm, transform=[size * cm / (x2 - x1), 0, 0, size * cm / (y2 - y1), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, p.c, 17.48 * cm, p._py(1.67 + size))
    caption = ("A autenticidade desta NFS-e pode ser verificada pela leitura deste código QR "
               "ou pela consulta da chave de acesso no portal nacional da NFS-e")
    for i, line in enumerate(_split(caption, 4.72, 6)[:3]):
        p.text(15.80, 3.50 + i * 0.24, line, REGULAR, 6)
    p.y = row_top


def _party_block(p: _Page, title: str, party: dict[str, str] | None, missing: str, with_im: bool = True, extra_row=None) -> None:
    if party is None:
        p.notice(missing)
        return
    first = [(X[1], COL, "CNPJ / CPF / NIF", party["doc"])]
    if with_im:
        first.append((X[2], COL, "Indicador Municipal (Inscrição)", party["im"]))
    first.append((X[3], COL, "Telefone", party["fone"]))
    rows = [
        first,
        [(X[0], WIDE, "Nome / Nome Empresarial", party["nome"]), (X[2], COL, "Município / Sigla UF", party["mun_uf"]),
         (X[3], COL, "Código IBGE / CEP", party["ibge_cep"])],
        [(X[0], WIDE, "Endereço", party["end"]), (X[2], WIDE, "E-mail", party["email"])],
    ]
    if extra_row:
        rows.append(extra_row)
    p.block(title, rows)


def _parties(p: _Page, n: _Nfse) -> None:
    prest_el = n.dps.find("n:prest", NS)
    emit_is_prest = n.dps_text("n:tpEmit") in (None, "1")
    prest = _party(prest_el, n.emit if emit_is_prest else None)
    sn = [(X[0], COL, "Simples Nacional na Data de Competência", _desc(OP_SIMP_NAC, _t(prest_el, "n:regTrib/n:opSimpNac"))),
          (X[1], 15.30, "Regime de Apuração Tributária pelo SN", _desc(REG_AP_TRIB_SN, _t(prest_el, "n:regTrib/n:regApTribSN")))]
    _party_block(p, "PRESTADOR / FORNECEDOR", prest, "", extra_row=sn)

    _party_block(p, "TOMADOR / ADQUIRENTE", _party(n.dps.find("n:toma", NS)),
                 "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")

    dest = n.dps_ibscbs.find("n:dest", NS) if n.dps_ibscbs is not None else None
    if dest is None and n.dps_ibscbs is not None and _t(n.dps_ibscbs, "n:indDest") == "0":
        p.notice("O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO")
    else:
        _party_block(p, "DESTINATÁRIO DA OPERAÇÃO", _party(dest), "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", with_im=False)

    _party_block(p, "INTERMEDIÁRIO DA OPERAÇÃO", _party(n.dps.find("n:interm", NS)),
                 "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")


def _service(p: _Page, n: _Nfse, description_height: float) -> None:
    c_trib_nac = n.dps_text("n:serv/n:cServ/n:cTribNac")
    c_trib_mun = n.dps_text("n:serv/n:cServ/n:cTribMun")
    nbs = n.dps_text("n:serv/n:cServ/n:cNBS")
    loc = n.dps_text("n:serv/n:locPrest/n:cLocPrestacao")
    pais = n.dps_text("n:serv/n:locPrest/n:cPaisPrestacao")
    place = _join(n.nfse_text("n:xLocPrestacao"), _uf(loc), "BR" if loc else pais)
    code = _join(f"{c_trib_nac[:2]}.{c_trib_nac[2:4]}.{c_trib_nac[4:]}" if c_trib_nac else None, c_trib_mun)
    nbs_fmt = f"{nbs[0]}.{nbs[1:5]}.{nbs[5:7]}.{nbs[7:]}" if nbs else None
    p.block("SERVIÇO PRESTADO", [[(X[1], COL, "Código de Tributação Nacional / Municipal", code),
                                   (X[2], COL, "Código da NBS", nbs_fmt),
                                   (X[3], COL, "Local da Prestação / Sigla UF / País", place)]])
    code_description = n.nfse_text("n:xTribMun") or n.nfse_text("n:xTribNac") or EMPTY
    p.text(X[0] + 0.05, p.y + 0.26, _fit(code_description, REGULAR, 7, FULL - 0.1))
    p.y += 0.38
    p.text(X[0] + 0.05, p.y + 0.22, "Descrição do Serviço", BOLD, 6)
    p.wrapped(p.y + 0.27, description_height - 0.27, _split(n.dps_text("n:serv/n:cServ/n:xDescServ") or EMPTY))
    p.y += description_height


def _issqn(p: _Page, n: _Nfse) -> None:
    if (n.dps_text("n:serv/n:cServ/n:cTribNac") or "").startswith("99"):
        p.notice("TRIBUTAÇÃO MUNICIPAL (ISSQN) - OPERAÇÃO NÃO SUJEITA AO ISSQN")
        return
    trib = n.dps_valores.find("n:trib/n:tribMun", NS)
    inc = n.nfse_text("n:cLocIncid")
    incidence = _join(n.nfse_text("n:xLocIncid"), _uf(inc), "BR" if inc else _t(trib, "n:cPaisResult"))
    reg_esp = n.dps_text("n:prest/n:regTrib/n:regEspTrib")
    special = [_desc(REG_ESP_TRIB, reg_esp) if reg_esp not in (None, "0") else None,
               _desc(TP_IMUNIDADE, _t(trib, "n:tpImunidade")),
               _desc(TP_SUSP, _t(trib, "n:exigSusp/n:tpSusp")), _t(trib, "n:exigSusp/n:nProcesso")]
    deductions = (_t(n.valores, "n:vCalcDR") or _t(n.valores, "n:vCalcAjusteBCISSQN")
                  or _t(n.dps_valores, "n:vDedRed/n:vDR") or _t(n.dps_valores, "n:vAjusteBC/n:vAjusteBCISSQN"))
    bm_calc = _t(n.valores, "n:vCalcBM") or _t(trib, "n:BM/n:vRedBCBM")
    discount = _t(n.dps_valores, "n:vDescCondIncond/n:vDescIncond")
    benefit = [_desc(TP_BM, _t(n.valores, "n:tpBM")), bm_calc, deductions, discount]

    rows = [[(X[1], COL, "Tipo de Tributação do ISSQN", _desc(TRIB_ISSQN, _t(trib, "n:tribISSQN"))),
             (X[2], WIDE, "Município / Sigla UF / País de Incidência do ISSQN", incidence)]]
    # NT 008 nota 5: these two rows may be dropped when every field in them is empty.
    if any(special):
        rows.append([(X[0], COL, "Regime Especial de Tributação do ISSQN", special[0]),
                     (X[1], COL, "Tipo de Imunidade do ISSQN", special[1]),
                     (X[2], COL, "Suspensão da Exigibilidade do ISSQN", special[2]),
                     (X[3], COL, "Número Processo Suspensão", special[3])])
    if any(benefit):
        rows.append([(X[0], COL, "Benefício Municipal", benefit[0]),
                     (X[1], COL, "Cálculo do BM", money(bm_calc) if bm_calc else None),
                     (X[2], COL, "Total Deduções/Reduções", money(deductions) if deductions else None),
                     (X[3], COL, "Desconto Incondicionado", money(discount) if discount else None)])
    rows.append([(X[0], COL, "BC ISSQN", money(_t(n.valores, "n:vBC"))),
                 (X[1], COL, "Alíquota Aplicada", percent(_t(n.valores, "n:pAliqAplic"))),
                 (X[2], COL, "Retenção do ISSQN", _desc(TP_RET_ISSQN, _t(trib, "n:tpRetISSQN"))),
                 (X[3], COL, "ISSQN Apurado", money(_t(n.valores, "n:vISSQN")))])
    p.block("TRIBUTAÇÃO MUNICIPAL (ISSQN)", rows)


def _federal(p: _Page, n: _Nfse) -> None:
    fed = n.dps_valores.find("n:trib/n:tribFed", NS)
    ret = _t(fed, "n:piscofins/n:tpRetPisCofins")
    csll, pis, cofins = (_dec(_t(fed, path)) for path in ("n:vRetCSLL", "n:piscofins/n:vPis", "n:piscofins/n:vCofins"))
    # NT 008 v1.02: with tpRetPisCofins = 1 the retained social contributions add PIS and COFINS.
    if ret == "1":
        social = sum((v for v in (csll, pis, cofins) if v is not None), Decimal(0)) if any((csll, pis, cofins)) else None
        pis_debit = cofins_debit = Decimal(0)
    else:
        social, pis_debit, cofins_debit = csll, pis, cofins
    rows = [[(X[1], COL, "IRRF", money(_t(fed, "n:vRetIRRF"))),
             (X[2], COL, "Contribuição Previdenciária - Retida", money(_t(fed, "n:vRetCP"))),
             (X[3], COL, "Contribuições Sociais - Retidas", money(social))]]
    competence = n.dps_text("n:dCompet") or ""
    if competence[:4] <= "2026":  # nota 6: PIS/COFINS own-debit row only up to competence 2026
        rows.append([(X[0], COL, "PIS - Débito Apuração Própria", money(pis_debit)),
                     (X[1], COL, "COFINS - Débito Apuração Própria", money(cofins_debit)),
                     (X[2], WIDE, "Descrição Contrib. Sociais - Retidas", _desc(TP_RET_PIS_COFINS, ret))])
    p.block("TRIBUTAÇÃO FEDERAL (EXCETO CBS)", rows)


def _ibs_cbs(p: _Page, n: _Nfse) -> None:
    g = n.ibscbs
    v = g.find("n:valores", NS) if g is not None else None
    tot = g.find("n:totCIBS", NS) if g is not None else None
    trib = n.ibs_trib
    exclusions = None
    if g is not None:
        parts = [_dec(x) for x in (
            _t(n.dps_valores, "n:vDescCondIncond/n:vDescIncond"),
            _t(v, "n:vCalcReeRepRes") or _t(v, "n:vCalcAjusteBCIBSCBS"),
            _t(n.valores, "n:vISSQN"),
            _t(n.dps_valores, "n:trib/n:tribFed/n:piscofins/n:vPis"),
            _t(n.dps_valores, "n:trib/n:tribFed/n:piscofins/n:vCofins"),
        )]
        exclusions = sum((x for x in parts if x is not None), Decimal(0))
    loc = _t(g, "n:cLocalidadeIncid")
    indicator = _join(_t(n.dps_ibscbs, "n:cIndOp"), loc, _t(g, "n:xLocalidadeIncid"), _uf(loc)) if g is not None else EMPTY
    red = " / ".join(percent(_t(v, p_)) for p_ in ("n:uf/n:pRedAliqUF", "n:mun/n:pRedAliqMun", "n:fed/n:pRedAliqCBS"))
    rows = [
        [(X[1], COL, "CST / cClassTrib", _join(_t(trib, "n:CST"), _t(trib, "n:cClassTrib"))),
         (X[2], WIDE, "Indicador de Operação / Código IBGE Incidência / Município Incidência / Sigla UF", indicator)],
        [(X[0], COL, "Exclusões e Reduções da Base de Cálculo", money(exclusions)),
         (X[1], COL, "Base de Cálculo Após Exclusões e Reduções", money(_t(v, "n:vBC"))),
         (X[2], COL, "Red. Alíquota IBS / Red. Alíquota CBS", red if g is not None else EMPTY),
         (X[3], COL, "Alíquota - IBS UF / IBS Mun", f"{percent(_t(v, 'n:uf/n:pIBSUF'))} / {percent(_t(v, 'n:mun/n:pIBSMun'))}" if g is not None else EMPTY)],
        [(X[0], COL, "Alíq. Efetiva Municipal - IBS", percent(_t(v, "n:mun/n:pAliqEfetMun"))),
         (X[1], COL, "Valor Apurado Municipal - IBS", money(_t(tot, "n:gIBS/n:gIBSMunTot/n:vIBSMun"))),
         (X[2], COL, "Alíq. Efetiva Estadual - IBS", percent(_t(v, "n:uf/n:pAliqEfetUF"))),
         (X[3], COL, "Valor Apurado Estadual - IBS", money(_t(tot, "n:gIBS/n:gIBSUFTot/n:vIBSUF")))],
        [(X[0], COL, "Valor Total Apurado - IBS", money(_t(tot, "n:gIBS/n:vIBSTot"))),
         (X[1], COL, "Alíquota - CBS", percent(_t(v, "n:fed/n:pCBS"))),
         (X[2], COL, "Alíquota Efetiva - CBS", percent(_t(v, "n:fed/n:pAliqEfetCBS"))),
         (X[3], COL, "Valor Total Apurado - CBS", money(_t(tot, "n:gCBS/n:vCBS")))],
    ]
    p.block("TRIBUTAÇÃO IBS / CBS", rows)


def _totals(p: _Page, n: _Nfse) -> None:
    tot = n.ibscbs.find("n:totCIBS", NS) if n.ibscbs is not None else None
    ibs, cbs = _dec(_t(tot, "n:gIBS/n:vIBSTot")), _dec(_t(tot, "n:gCBS/n:vCBS"))
    top = p.y
    p.hline(top)
    p.shade(X[0], top, COL, ROW)
    p.text(X[0] + 0.05, top + 0.34, "VALOR TOTAL DA NFS-E", BOLD, 7)
    p.field(X[1], top, COL, "VALOR DA OPERAÇÃO / SERVIÇO", money(n.dps_text("n:valores/n:vServPrest/n:vServ")), label_size=7)
    p.field(X[2], top, COL, "Desconto Incondicionado", money(_t(n.dps_valores, "n:vDescCondIncond/n:vDescIncond")))
    p.field(X[3], top, COL, "Desconto Condicionado", money(_t(n.dps_valores, "n:vDescCondIncond/n:vDescCond")))
    top += ROW
    p.shade(X[3], top, COL, ROW)
    p.field(X[0], top, COL, "Total das Retenções (ISSQN / Federais)", money(_t(n.valores, "n:vTotalRet")))
    p.field(X[1], top, COL, "VALOR LÍQUIDO DA NFS-e", money(_t(n.valores, "n:vLiq")), label_size=7)
    p.field(X[2], top, COL, "Total do IBS/CBS", money(ibs + cbs) if ibs is not None and cbs is not None else EMPTY)
    p.field(X[3], top, COL, "VALOR LÍQUIDO DA NFS-e + IBS/CBS", money(_t(tot, "n:vTotNF")), label_size=7)
    p.y = top + ROW


def _approximate_taxes(n: _Nfse) -> str:
    tot = n.dps_valores.find("n:trib/n:totTrib", NS)
    prefix = "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: "
    if _t(tot, "n:vTotTrib/n:vTotTribFed") is not None:
        values = [money(_t(tot, f"n:vTotTrib/n:vTotTrib{s}")) for s in ("Fed", "Est", "Mun")]
    elif _t(tot, "n:pTotTrib/n:pTotTribFed") is not None:
        values = [percent(_t(tot, f"n:pTotTrib/n:pTotTrib{s}")) for s in ("Fed", "Est", "Mun")]
    elif _t(tot, "n:pTotTribSN") is not None:
        # The Simples Nacional total is a single percentage with no federal/state/municipal split.
        return prefix + f"Simples Nacional: {percent(_t(tot, 'n:pTotTribSN'))}"
    else:
        values = [EMPTY] * 3
    return prefix + f"Federais: {values[0]}; Estaduais: {values[1]}; Municipais: {values[2]}"


def _complementary(n: _Nfse) -> str:
    serv = n.dps.find("n:serv", NS)
    items = [
        ("Inf. Cont.", _t(serv, "n:infoCompl/n:xInfComp")),
        ("NFS-e Subst.", n.dps_text("n:subst/n:chSubstda")),
        ("Doc. Ref.", _t(serv, "n:infoCompl/n:docRef")),
        ("Cod. Obra", _t(serv, "n:obra/n:cObra")),
        ("Insc. Imob.", _t(serv, "n:obra/n:inscImobFisc") or _t(n.dps_ibscbs, "n:imovel/n:inscImobFisc")),
        ("Cod. Evt.", _t(serv, "n:atvEvento/n:idAtvEvt")),
        ("Doc. Tec.", _t(serv, "n:infoCompl/n:idDocTec")),
        ("Núm. Ped.", _t(serv, "n:infoCompl/n:xPed")),
        ("Item Ped.", ", ".join(e.text for e in serv.iterfind("n:infoCompl/n:gItemPed/n:xItemPed", NS) if e.text) or None),
        ("Inf. A. T. Mun.", n.nfse_text("n:xOutInf")),
    ]
    return " | ".join(f"{label}: {value}" for label, value in items if value)


def _stub(p: _Page, n: _Nfse) -> None:
    top = STUB_TOP
    p.c.setLineWidth(0.5)
    p.c.rect(X[0] * cm, p._py(top + 0.67), FULL * cm, 0.67 * cm)
    p.c.line(X[1] * cm, p._py(top), X[1] * cm, p._py(top + 0.67))
    p.c.line(X[2] * cm, p._py(top), X[2] * cm, p._py(top + 0.67))
    p.text(X[0] + 0.05, top + 0.28, "DATA CIENTIFICAÇÃO:", BOLD, 7)
    p.text(X[1] + 0.05, top + 0.28, "IDENTIFICAÇÃO E ASSINATURA", BOLD, 7)
    p.field(X[2], top, WIDE, "Nº NFS-e / CHAVE NFS-e", f"{n.nfse_text('n:nNFSe') or EMPTY} / {n.access_key}", label_size=7)


def _watermark(p: _Page, mark: Watermark) -> None:
    p.c.saveState()
    p.c.setFillGray(WATERMARK_GREY)
    p.c.setFont(REGULAR, 90)
    p.c.translate(10.5 * cm, 15.5 * cm)
    p.c.rotate(55)
    p.c.drawCentredString(0, 0, mark.value)
    p.c.restoreState()


def _render_danfse(nfse_xml: bytes, watermark: Watermark | None = None) -> bytes:
    """Render the DANFSe of an authorized NFS-e (the XML SEFIN returns) as a one-page A4 PDF."""
    n = _Nfse(etree.fromstring(nfse_xml, etree.XMLParser(resolve_entities=False, no_network=True)))
    buffer = io.BytesIO()
    c = Canvas(buffer, pagesize=A4, pageCompression=1)
    c.setTitle(f"DANFSe {n.nfse_text('n:nNFSe') or ''}".strip())
    p = _Page(c)
    if watermark is not None:
        _watermark(p, watermark)

    c.setLineWidth(1)
    c.rect(BORDER * cm, BORDER * cm, (21.0 - 2 * BORDER) * cm, (PAGE_H - 2 * BORDER) * cm)

    _header(p, n)
    _identification(p, n)
    _parties(p, n)

    description_lines = len(_split(n.dps_text("n:serv/n:cServ/n:xDescServ") or EMPTY))
    description_height = min(max(0.60 + description_lines * 0.30, 1.6), 5.0)
    _service(p, n, description_height)
    _issqn(p, n)
    _federal(p, n)
    _ibs_cbs(p, n)
    _totals(p, n)

    p.hline(p.y)
    p.text(X[0] + 0.05, p.y + 0.30, "INFORMAÇÕES COMPLEMENTARES", BOLD, 7)
    info_top = p.y + 0.40
    available = STUB_TOP - 0.15 - info_top - 0.30  # keep the last line for the fixed tax totals
    info = _complementary(n)
    p.wrapped(info_top, available, _split(info) if info else [])
    p.text(X[0] + 0.05, STUB_TOP - 0.25, _fit(_approximate_taxes(n), REGULAR, 7, FULL - 0.1))
    _stub(p, n)

    c.showPage()
    c.save()
    return buffer.getvalue()


def render_danfse(nfse_xml: bytes, watermark: Watermark | None = None, *, font_set: FontSet | None = None) -> bytes:
    """Render an authorized NFS-e as a one-page PDF.

    Optional ``font_set`` supplies caller-owned TTF/TTC fallbacks. Without it,
    glyphs outside Helvetica's WinAnsi coverage fail with a PII-safe error.
    """
    token = activate(font_set if font_set is not None else DEFAULT_FONT_SET)
    try:
        return _render_danfse(nfse_xml, watermark)
    finally:
        deactivate(token)
