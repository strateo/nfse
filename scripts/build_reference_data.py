"""Regenerate ``src/nfse_nacional/data/`` from official gov.br files.

    python scripts/build_reference_data.py ANEXO_A.xlsx LOGO.png

- ANEXO_A.xlsx: "ANEXO A - MUNICIPIO_IBGE-PAISES_ISO2 (SNNFSe)" from Documentação Atual.
- LOGO.png: "Logo - NFS-e - Horizontal.png" from Biblioteca > Documentação técnica > Logos da NFS-e.
"""

from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

DATA = Path(__file__).resolve().parent.parent / "src" / "nfse_nacional" / "data"
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
LOGO_WIDTH = 600  # px; printed at 4 cm wide, so ~380 dpi


def read_sheets(path: Path) -> dict[str, list[dict[str, str]]]:
    z = zipfile.ZipFile(path)
    shared = [
        "".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)
    ]
    targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = {}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS):
        target = targets[sheet.get(f"{{{NS['r']}}}id")].lstrip("/").removeprefix("xl/")
        rows = []
        for row in ET.fromstring(z.read("xl/" + target)).find("m:sheetData", NS):
            cells = {}
            for c in row:
                v = c.find("m:v", NS)
                if v is not None:
                    cells[re.match(r"[A-Z]+", c.get("r")).group(0)] = shared[int(v.text)] if c.get("t") == "s" else v.text
            rows.append(cells)
        sheets[sheet.get("name")] = rows
    return sheets


# IBGE state codes (first two digits of a município code). The annex leaves its UF column
# empty on most rows, so UF is derived from the code and checked against the rows that have it.
UF_BY_IBGE = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
    "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS",
    "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


def main(anexo_a: Path, logo: Path) -> None:
    sheets = read_sheets(anexo_a)
    municipios = []
    for r in sheets["TAB.MUN_IBGE"][1:]:
        code = r.get("D", "").strip()
        if not re.fullmatch(r"[0-9]{7}", code):
            continue
        uf = UF_BY_IBGE[code[:2]]
        if r.get("B", "").strip() not in ("", uf):
            raise SystemExit(f"UF mismatch for {code}: annex says {r['B']!r}, code says {uf!r}")
        municipios.append((code, r["C"].strip(), uf))
    municipios.sort()
    paises = sorted(
        (r["A"].strip(), r["B"].strip())
        for r in sheets["TAB.PAÍS_ISO2"][1:]
        if re.fullmatch(r"[A-Z]{2}", r.get("A", "").strip())
    )
    if len(municipios) < 5000 or len(paises) < 200:
        raise SystemExit(f"unexpected annex content: {len(municipios)} municípios, {len(paises)} países")
    DATA.mkdir(exist_ok=True)
    (DATA / "municipios.tsv").write_text("".join("\t".join(r) + "\n" for r in municipios), encoding="utf-8")
    (DATA / "paises.tsv").write_text("".join("\t".join(r) + "\n" for r in paises), encoding="utf-8")

    with Image.open(logo) as img:
        img = img.convert("RGBA")
        img.resize((LOGO_WIDTH, round(img.height * LOGO_WIDTH / img.width)), Image.LANCZOS).save(
            DATA / "nfse_logo.png", optimize=True
        )
    print(f"{len(municipios)} municípios, {len(paises)} países, logo {LOGO_WIDTH}px")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
