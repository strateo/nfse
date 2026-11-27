# NFS-e Python client

`nfse_nacional` is a Python 3.11+ client for NFS-e Padrão Nacional (SEFIN Nacional
and ADN). Keep private integrations and business research out of this repo.

Keep an explicit production release for Checkout and automate the tagging and
deployment. Start the workflow with an explicit release action. A merge to
`main` alone does not deploy Checkout to production.

## Contracts and checks

- Keep official names for dataclass fields that map one-to-one to XML.
  Use English elsewhere. Use `Decimal` for amounts and avoid silent rounding.
- Select layouts through `environment.py` and keep layout-specific DPS changes
  in the `dps.py` branches. Keep both bundled layouts covered in
  `tests/test_dps.py`, including the `Layout.NT009` compatibility name for
  `Layout.NT009_R1`. Add offline validators only for substantiated rules and cite
  the SEFIN error code in `dps._validate`.
- Use the existing venv to run
  `.venv/Scripts/python.exe -W error::ResourceWarning -m unittest discover -s tests`.
  Tests use a throwaway PKI and a local mTLS server. Any dependency change
  requires approval.

## Schema updates

- Import the official gov.br archive for the matching environment. Keep
  `src/nfse_nacional/schemas/{producao,producao_restrita}/` byte-for-byte with
  upstream. Apply compatibility patches in `schemas.py` and run the tests.
- Remove the `TSSerieDPS` resolver patch once the production bundle corrects
  its `^0{0,4}\d{1,5}$` pattern. Regenerate reference tables and the logo
  with `scripts/build_reference_data.py`.
