# Changelog

## 0.1.2 (2026-11-09)

- Enforce own-application DPS series 1–49999 (E0010) without narrowing the
  structural identifier helper used for other emission channels.
- Reject competence after the Brasília emission date (E0015), resolving the
  default emission timestamp once for both validation and XML serialization.
- Omit pAliq in the canonical ME/EPP examples and test fixture; preserve explicit
  rates because E0617/E0625 depend on municipal configuration and benefit rules.
- Document the announced December 2026 NT 009 transition, release-1-only test
  layout support, partial IBS/CBS model and unverified post-transition acceptance.

## 0.1.1 (2026-11-09)

- Bind XML signature verification to the primary fiscal payload, reject duplicate
  reference IDs and unsupported algorithms, and report malformed signatures clearly.
- Preserve the TransportError recovery path for interrupted HTTP reads and corrupt
  or empty compressed XML. Reject malformed or stalled ADN batches while preserving
  empty-queue responses with absent, null or empty document lists.
- Require CST for supplied PIS/COFINS values, reject float tax totals, and validate
  complete CPF/CNPJ strings. Handle Retry-After case-insensitively and reject infinities.
- Render supported WinAnsi punctuation and reject unsupported control characters.
- Keep smoke-test numbering monotonic, preserve signed request bytes, and use the
  Brasília competence date. Clarify manual recovery after an interrupted run.
- Correct PDF and cursor examples and document alphanumeric-CNPJ schema limitations.

Live SEFIN/ADN acceptance and issuer tax treatment remain unverified. Version 0.1.0
remains affected by the original defects; upgrade to 0.1.1 for these fixes.

## 0.1.0

Initial public release of the Python 3.11+ NFS-e Nacional client.

- SEFIN issuance, query, recovery and cancellation; ADN distribution.
- DPS builders for production and Produção Restrita layouts, official XSD
  validation, certificate handling and XML signatures.
- Local DANFSe PDF generation and explicit Unicode fallback fonts.
- Offline test suite with synthetic fiscal documents and temporary mTLS PKI.
- MIT project license, third-party attribution and GitHub Actions test matrix.

Validation: 73 tests passed on Windows/Python 3.14 against both source and the
installed release wheel. Wheel contents include all 20 XSDs, reference data and
the MIT license. Live government API acceptance and tax treatment are unverified.
