# Contributing

Use Python 3.11 or newer. Create a virtual environment and install the checkout
with `python -m pip install -e .`, then run:

```sh
python -W error::ResourceWarning -m unittest discover -s tests
```

Tests use synthetic documents, a temporary PKI and local mTLS servers. Never attach
real certificates, taxpayer records, signed invoices, passwords or smoke output
to issues or pull requests. Replace private data with synthetic examples.

Three font integration tests require Windows Malgun Gothic and Arial and report
explicit skips on hosts without those fonts. Windows CI covers these tests.

Keep amounts as Decimal and cover both supported layouts when changing DPS rules.
Do not hand-edit official schemas or generated reference data. The regeneration
script additionally uses Pillow, supplied transitively by ReportLab.

Real certificate smoke runs send fiscal requests. Run them only with the issuer's
authorization, after reviewing every argument; they are excluded from CI.
