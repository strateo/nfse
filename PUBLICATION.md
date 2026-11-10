# Publication checklist

The initial public export excluded private integration modules and tests,
internal business research, local environments and package build metadata.
No private Git history, remote, credentials or live fiscal output were copied.
`docs/research/` contains public fiscal-specification findings and official sources,
added to this repository after that initial export.

The project code is licensed under MIT; see LICENSE. Third-party material retains
its own terms.

Release process:

- Retain the upstream fixture's MIT notice and government attribution in
  THIRD_PARTY_NOTICES.md. These were checked for the initial release.
- Run CI on GitHub; local results do not establish hosted CI success.
- Review this exact repository and publish only its contents to
  https://github.com/strateo/nfse. Do not upload the parent working directory.

No live government API verification or production certification is claimed.
