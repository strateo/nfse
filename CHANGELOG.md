# Changelog

## Unreleased

- Reject `TotalTax.mei()` for a Não Optante provider (E0713). The builder accepted
  it and SEFIN rejects it. The MEI and Não Optante guards now cite E0710 and E0713.
- Accept `TotalTax.percent()` and `TotalTax.amount()` for MEI and ME/EPP. The
  official rules forbid only `pTotTribSN` for MEI (E0710) and `indTotTrib` for
  ME/EPP (E0712).
- Stop requiring a national address for a CNPJ recipient. Both official rule sheets
  mark E0235 as not executed when SEFIN receives a provider DPS. Callers that want
  the address on every note must check it themselves.
- Document the common rejections, the regime rules for `totTrib` and the TLS
  behaviour of SEFIN and ADN.
- Read the e-CNPJ holder's CNPJ from the ICP-Brasil SubjectAlternativeName field
  (OID 2.16.76.1.3.3) with `Certificate.cnpj`, including alphanumeric CNPJs.
- Add `Certificate.check()`, raising `CertificateError` with a stable `code` for a
  key that does not match the certificate, an RSA key under 2048 bits, a subject CN
  that contradicts the e-CNPJ field, a different required CNPJ, a certificate outside
  its validity window, or one without digital-signature (`key-usage`) or TLS
  client-auth (`client-auth`) usage. An invalid `cnpj` argument raises ValueError.
  The ICP-Brasil chain and revocation are not checked locally.
- The Produção Restrita smoke runner reads the CNPJ from the e-CNPJ field instead of
  the subject CN, and runs `check()` before building a DPS. A certificate without the
  e-CNPJ field now needs `--cnpj`. A failed check reports its code.

## 0.1.3 (2026-11-10)

- Identify the bundled NT 009 release 1 with Layout.NT009_R1 and exact release
  metadata, preserving the existing Layout.NT009 compatibility name.
- Add optional persisted-document recovery, cancellation-event query, bounded
  ADN progression/exhaustion probes and a sanitized acceptance summary to the
  Produção Restrita smoke runner. Document the issuer-authorized test procedure.

- Keep concurrent XSD validation diagnostics bound to their own document.
- Normalize oversized ADN NSU conversion failures as TransportError and reject
  non-finite ISS rates before fiscal comparisons.
- Bound long-word wrapping work in DANFSe PDFs while preserving greedy line breaks.

- Compose E0625/E0631 and E0621/E0628 for the current ME/EPP model with
  SN-assessed ISS and no municipal-benefit group: forbid a rate without
  withholding, require it with withholding, and enforce the 1.80% minimum.
- Correct the prior municipal-rule analysis using the complementary inactive-
  municipality rules; preserve other assessment regimes' existing behavior.
- Report forbidden withholding (E0580/E0588) before asking for a missing rate
  within the new SN-assessed ISS branch.
- Document the default timestamp's five-second midnight window and distinguish
  public fiscal research from excluded private business research.

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
