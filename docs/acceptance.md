# Layout migration and fiscal acceptance

This is the execution record and runbook for [issue #4](https://github.com/strateo/nfse/issues/4).
Offline tests, an HTTP acceptance response and issuer tax compliance are separate
claims. No live government calls were made while preparing this runbook.

## Published layouts checked on 2026-11-09

| Public API | Exact bundled release | Officially documented availability |
| --- | --- | --- |
| `Layout.V1_01` | `v1.01-20260209` | Production; NT 004 plus the NT 007 retention domain, per the October 29 notice |
| `Layout.NT009_R1` | `nt009-r1-20261020` | Produção Restrita only; NT 009 v1.0 release 1 |
| `Layout.NT009` | Compatibility alias of `Layout.NT009_R1` | Same release 1; its existing enum name and value remain unchanged |
| Later NT 009 releases | Not implemented | No matching release-2 XSD linked on the official pages checked below |

`environment.layout.release` identifies the exact bundled release. Neither the
environment nor the alias changes with the calendar. The October 29 notice
targets December 1 for releases 1–3 and says a formal calendar will be published;
it does not establish an exact rejection date for old XML.

The published ZIPs were fetched in memory and hashed. All 10 XSD files bundled
from each archive matched their upstream bytes; no official file was edited.
Production's ZIP also contains seven XSDs outside the current package's scope.

| Official archive | SHA-256 |
| --- | --- |
| [Production 2026-02-09](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/nfse-esquemas_xsd-v1-01-20260209.zip/@@download/file) | `e7935cbd9470527c6cc32984c1b2263e614183bf0139ce2733eaaed2de9a8072` |
| [Produção Restrita 2026-10-20](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita/esquemas_prod_restrita_20261020.zip/@@download/file) | `25fdcecc651a67a216496ab10d7566631e6ed10f721e37d62c7845dadb0ea267` |

Sources checked: [production documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual),
[test documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita),
[RTC specifications](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc),
[deployment updates](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/atualizacoes-e-implantacoes),
and the [October 29 notice](https://www.gov.br/nfse/pt-br/noticias/nfs-e-nacional-confira-oas-rientacoes-sobre-leiaute-em-producao-regras-de-preenchimento-de-ibs-cbs-e-status-da-nota-tecnica-009).

## Later-layout implementation boundary

The [NT 009 v1.01 specification](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nota-tecnica-009-se-cgnfs-e-v-1-01.pdf)
describes `opSimpNac=4`, `regApIBSCBSSN`, `cAtvSN`, and relocation of
`indDest`/`dest`. These must not be emitted into the release-1 schemas or enabled
by a date switch. The current IBS/CBS model remains partial.

When the corresponding official XSD is published:

1. Record its URL, download date, SHA-256 and documented environment availability.
2. Add an explicit layout for that release, keeping `V1_01` and `NT009_R1` behavior.
   Copy the upstream schema files unchanged into a separate bundle.
3. Implement the new fields and element positions against that bundle, including
   applicable mandatory recipient/acquirer data. Revisit BM-related guards if
   municipal-benefit support is added.
4. Test build, XSD validation, signing and regression compatibility for every
   supported layout. Confirm `opSimpNac=4` and relocated fields fail under older
   layouts instead of being silently omitted.
5. Change an environment's default only after its deployment is documented and
   acceptance is tested for that exact release. Keep an evidence record below.

## Issuer-authorized Produção Restrita run

Before a live run, obtain explicit issuer authorization for emission and, if
used, cancellation. Use an appropriate A1 certificate and reviewed issuer,
recipient, service and tax inputs. This document and issue #4 grant no such
authorization. The script uses Produção Restrita endpoints only.

Use the parent/project virtual environment on this checkout with `PYTHONPATH`
pointing at its `src`, or install the reviewed public package in your own
environment. On this Windows checkout the verified offline command is:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
..\.venv\Scripts\python -W error::ResourceWarning -m unittest discover -s tests
```

Start with a private output directory and the dry run below. Replace the
placeholders with reviewed values. The password comes from a hidden prompt or
`NFSE_PFX_PASSWORD`; do not place it in command-line arguments.

```powershell
..\.venv\Scripts\python scripts/smoke_producao_restrita.py `
  --pfx '<private A1 path>' --c-loc-emi '<issuer IBGE code>' `
  --op-simp-nac 3 --p-tot-trib-sn '<reviewed percentage>' `
  --c-trib-nac '<reviewed national service code>' `
  --out '<private artifact directory>' --dry-run
```

Supply the recipient/address and other fiscal options required for the reviewed
case; `--help` lists them. A successful dry run proves only construction, local
XSD validation, signing and persistence. It makes no SEFIN/ADN requests.

After review and issuer authorization, run the reviewed inputs without
`--dry-run`, adding `--check-recovery --cancel --adn-last-nsu <reviewed cursor>
--adn-max-batches 10` for the full probe. `--cancel` registers cancellation of
the newly issued test document. A new invocation allocates a new nDPS; after an
interruption, recover the saved signed DPS with `SefinClient.recover()` instead
of blindly rerunning issuance. The number file is a serial test-run counter,
not a production or concurrent allocator.

The runner records these observations:

- Issuance and query: compare the queried XML to the saved emission response,
  using canonical XML to ignore declaration/attribute-order differences.
- Same-document recovery: re-read the saved signed bytes, recover using the same
  DPS Id, and require the same access key and fiscal XML. This may re-send the
  same bytes if the service reports the Id absent; it never allocates a new Id.
- Cancellation: persist the signed request, register it, then query event 101101
  and compare the returned event with the registration response.
- ADN: follow strictly increasing cursors until exhaustion or the request bound.
  An empty queue without any document batch is `not_observed` for progression.
  Reaching the bound is `not_observed` for exhaustion, not proof of an empty queue.
  There are no retry waits or unbounded polling. This probe does not persist ADN
  documents or a durable cursor; it is not a distribution consumer.

## Private evidence and public summary

Keep `report.json`, signed DPS, NFS-e XML, cancellation XML and PDF files private.
They can contain tax identifiers, names, addresses, service details, keys and
server response bodies. The output directory and console logs need the same
private handling as the certificate's fiscal data.

`acceptance-summary.json` is an allowlisted summary: package version, environment,
exact layout release, UTC timestamps, mode, fixed check names, outcomes and
recognized response codes. It includes HTTP status only when exposed by a
failure; success HTTP status is not invented. It excludes identifiers, XML,
free-text server descriptions, raw bodies and paths. Review the summary before
publishing it; the tool never uploads it. The package version does not identify
an unreleased checkout: record the reviewed commit separately.

`passed`, `failed`, `not_run` and `not_observed` have distinct meanings. A zero
exit code means the requested operations completed; it does not establish full
acceptance if recovery/cancellation were omitted or ADN progression/exhaustion
was not observed. A recovered transport error remains visible as an earlier
failed observation rather than being erased.

For an authorized public acceptance record, add only:

- Reviewed commit/release and the inspected summary, with its exact environment,
  layout and UTC run date.
- Each requested step's observed result and response codes. Mark unrun or
  unobserved steps explicitly; do not infer eventual ADN availability.
- Fixture provenance and a separate disclosure review before adding any
  sanitized regression fixture. Keep real fiscal documents out of the repository.
- Remaining limits: local signature checks do not establish certificate-chain
  trust; technical API acceptance does not establish issuer tax compliance.

Issue #4 remains open until a selected later layout has been implemented against
its official bundle and the issuer-authorized acceptance evidence is recorded.
