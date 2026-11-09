# Changelog

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
