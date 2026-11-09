# NFS-e Nacional

<p align="center">
  <img src="assets/icon.png" width="128" alt="NFS-e Nacional icon">
</p>

<p align="center">
  Python client for issuing, querying, and cancelling Brazil’s national-standard service invoices.
</p>

<p align="center">
  <a href="https://github.com/strateo/nfse/actions/workflows/tests.yml"><img src="https://github.com/strateo/nfse/actions/workflows/tests.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/strateo/nfse/releases/tag/v0.1.2"><img src="https://img.shields.io/badge/release-v0.1.2-104A58" alt="v0.1.2"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-104A58" alt="MIT"></a>
</p>

<p align="center">
  <a href="README.md">Português (Brasil)</a> · <strong>English (US)</strong>
</p>

Python 3.11+ client for Brazil's NFS-e Padrão Nacional. It covers issuing through SEFIN Nacional, cancellation, queries, and DF-e distribution through ADN.

Independent, early-stage software; not an official government SDK. Offline tests do not establish acceptance by SEFIN, production readiness, or the correct tax treatment for a particular issuer. Consult the [official documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica) for current requirements.

Dependencies: `lxml`, `cryptography`, `reportlab` (DANFSe PDF). HTTP uses the standard library.

Install from a checkout with `python -m pip install .`. This repository provides a library, not a hosted issuer service. Persist documents and allocate DPS numbers in your application.

## Highlights

- DPS builders for production and Produção Restrita layouts.
- A1 certificate handling, XML signatures and official XSD validation.
- SEFIN issuance, recovery, queries and cancellation; ADN distribution.
- Local DANFSe PDFs with explicit Unicode fallback fonts.
- Decimal amounts and offline validation guards.

## Quick start

Clone this repository, then install it in a virtual environment:

```sh
git clone https://github.com/strateo/nfse.git
cd nfse
python -m venv .venv
.venv/Scripts/python -m pip install .
```

On Linux and macOS, use `.venv/bin/python`. Python 3.11 or newer is required.

## Alphanumeric CNPJ schema limitations

The CPF/CNPJ validators and the recipient CNPJ field accept alphanumeric CNPJ,
but this does not establish full issuance support for an issuer whose CNPJ contains
letters. The production bundle dated 2026-02-09 retains numeric-only DPS, NFS-e
and event identifier patterns. In the 2026-10-20 test bundle, the `chNFSe` pattern
also disagrees with the CNPJ position in related identifiers. These cases can fail
local XSD validation; live SEFIN acceptance is unverified. Official schemas are
preserved without speculative broadening of these patterns. Check the bundles in
the [official documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual).

## Usage

`build_dps` issues through the own-application channel: use series 1 through 49999
(E0010). Competence must not be after the Brasília emission date (E0015).
Identifiers for other channels remain available through `dps_id`.

The example omits `pAliq`. Rules E0617/E0625 depend on the incidence municipality's
configuration and, for E0625, municipal benefits and withholding. Check those
parameters before supplying a rate: the library preserves your value and does
not determine its municipal admissibility.

```python
from datetime import date
from decimal import Decimal

from nfse_nacional import dps as d, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional.client import SefinClient
from nfse_nacional.environment import PRODUCAO_RESTRITA as ENV
from nfse_nacional.errors import RejectionError, TransportError

cert = Certificate.from_pfx_file("empresa.pfx", "senha")
sefin = SefinClient(cert.ssl_context(), ENV)

note = d.Dps(
    cLocEmi="3550308", serie="900", nDPS=next_number(),  # atomic counter per (CNPJ, serie)
    dCompet=date(2026, 11, 3),
    prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_ME_EPP, CNPJ="11222333000181", regApTribSN="1"),
    serv=d.Service(cTribNac="010101", xDescServ="Consultoria em TI", cLocPrestacao="3550308"),
    vServ=Decimal("1500.00"),
    tribMun=d.MunicipalTax(),
    totTrib=d.TotalTax.simples(Decimal("6.00")),
    toma=d.Taker(xNome="Acme Ltda", CNPJ="11444777000161",
                 end=d.Address(xLgr="Av. Paulista", nro="1000", xBairro="Bela Vista",
                               cMun="3550308", CEP="01310100")),
)

root = d.build_dps(note, ENV)          # offline rule checks
signing.sign(root, cert)
schemas.validate(root, ENV.layout)     # official XSD for this environment
xml = signing.to_bytes(root)
save_pending(note.id, xml)             # persist BEFORE sending

try:
    result = sefin.emit(xml)
except TransportError:
    result = sefin.recover(xml, note.id)  # same XML, never a new nDPS
except RejectionError as e:
    print(e.codes)                         # e.g. ['E0712']
```

Cancellation uses `events.build_cancellation(...)`, then `signing.sign` and `sefin.register_event(key, xml)`. Distribution uses `AdnClient(cert.ssl_context(), ENV).fetch_dfe(last_nsu)` in a loop until `batch.exhausted`, persisting `batch.last_nsu` after each batch only when it is not `None`.

### DANFSe (PDF)

The PDF is rendered locally as a single A4 page targeting NT 008/2026 v1.02:

```python
from nfse_nacional.danfse import Watermark, render_danfse

pdf = render_danfse(result.nfse_xml)                       # authorized NFS-e XML from emit()
pdf = render_danfse(result.nfse_xml, watermark=Watermark.CANCELLED)  # after a 101101 cancellation event
```

Cancellation and substitution are separate events, not part of the NFS-e XML, so pass the watermark yourself. In Produção Restrita the document says "NFS-e SEM VALIDADE JURÍDICA" automatically.

## Smoke test in Produção Restrita

`scripts/smoke_producao_restrita.py` runs the whole cycle against the test environment with your real A1 certificate:
- certificate check
- ADN mTLS check
- `HEAD /dps`
- build, validate and sign the DPS
- emit, then render the DANFSe
- query the note back
- with `--cancel`, cancel it

It only ever targets Produção Restrita, where notes have no legal validity. The PFX password comes from `NFSE_PFX_PASSWORD` or a hidden prompt.

```bash
.venv/Scripts/python scripts/smoke_producao_restrita.py --pfx empresa.pfx --c-loc-emi 3550308 --op-simp-nac 3 --p-tot-trib-sn 6.00 --c-trib-nac 010101 --toma-cnpj 11444777000161 --toma-street "Av. Paulista" --toma-number 1000 --toma-district "Bela Vista" --toma-c-mun 3550308 --toma-cep 01310100 --cancel
```

Output:
- Documents, `report.json` (each step with its E-codes) and the DPS number state go to `smoke-output/`.
- Every signed document is saved before it is sent.
- `--dry-run` stops before the network.
- `--profile RSA_SHA256` tests the alternative signature profile.

The smoke counter is a single-operator test helper, not a concurrent production
allocator. A real A1/Produção Restrita run is an external fiscal action requiring its
own approval. Its XSD dry run does not prove CEP consistency, account authority or tax
classification. All automated smoke tests use throwaway PKI and stop before network.

## Development

### Production layout transition

Status checked on 2026-11-09: the official October 29 notice keeps NT 004/007 in
production and targets NT 009 releases 1 through 3 for production on 2026-12-01,
with staged deployment and a formal schedule to follow. `PRODUCAO` still uses
the earlier layout; `Layout.NT009` covers only test release 1. This version does
not implement the later NT 009 v1.01 adjustments or switch layouts by date.
Production deployment requires checking the schedule and matching official
bundle; compatibility after the transition has not been established.

ADN can accept missing IBS/CBS data even from obligated issuers. Supplying the
group requires the applicable fields and classifications; the current model is
partial, and technical acceptance does not establish tax compliance.
See the [official notice](https://www.gov.br/nfse/pt-br/noticias/nfs-e-nacional-confira-oas-rientacoes-sobre-leiaute-em-producao-regras-de-preenchimento-de-ibs-cbs-e-status-da-nota-tecnica-009)
and [verified rules](docs/research/nfse-nacional-learnings.md).

### Local tests

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -W error::ResourceWarning -m unittest discover -s tests
```

On Linux and macOS, use `.venv/bin/python` instead of `.venv/Scripts/python`.
The usage example is an application sketch: implement `next_number()` and
`save_pending()` with durable storage before using it.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled material. See [PUBLICATION.md](PUBLICATION.md) for outstanding publication gates.

## License

Project code is available under the [MIT License](LICENSE). Bundled third-party
material retains its own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
