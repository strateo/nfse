# Third-party material

- `src/nfse_nacional/schemas/`: official government NFS-e XSD bundles, retained
  unmodified. Sources: [production documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual)
  and [test documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita).
- `src/nfse_nacional/data/`: municipality/country tables derived from Anexo A
  and the official NFS-e logo. See `scripts/build_reference_data.py` for inputs.
  Tables are factual reference extracts; the logo is resized for PDF rendering.
  The logo does not imply endorsement.
- `tests/fixtures/pynfsenacional_golden_dps_signed.xml`: fixture attributed to
  [Gomes343/PyNFSeNacional](https://github.com/Gomes343/PyNFSeNacional), reported
  under MIT, copyright 2026 Murilo Gomes Teixeira. The full upstream notice is
  retained in `tests/fixtures/LICENSE.PyNFSeNacional`; preserve it and the fixture README.

The government portal credits its content under
[CC BY-ND 3.0](https://creativecommons.org/licenses/by-nd/3.0/), as stated in the
documentation page footer (checked 2026-11-09). Government material is attributed
to Portal Nacional da NFS-e / Brazilian government and is not relicensed as MIT.
No endorsement or rights to use government marks beyond identification are claimed.
The project's MIT license does not override third-party terms.
