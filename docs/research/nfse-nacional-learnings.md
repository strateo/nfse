# Verified fiscal rules and rollout limits

Verified on 2026-11-09 against official published material. These are offline
rule checks and specification findings, not live SEFIN/ADN acceptance evidence.

## Sources

- [Production Anexo I, 2026-02-09](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/anexo_i-sefin_adn-dps_nfse-snnfse-v1-01-20260209.xlsx/@@download/file).
- [NT 009 v1.01 Anexo VI](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/anexovi-leiautesrn_rtc_ibscbs-v1-04-01-nt009.xlsx/@@download/file).
- [October 29 production and IBS/CBS notice](https://www.gov.br/nfse/pt-br/noticias/nfs-e-nacional-confira-oas-rientacoes-sobre-leiaute-em-producao-regras-de-preenchimento-de-ibs-cbs-e-status-da-nota-tecnica-009).
- [NT 009 v1.01 specification](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nota-tecnica-009-se-cgnfs-e-v-1-01.pdf).

The spreadsheets' rule sheet is `xl/worksheets/sheet5.xml`. Row numbers below
are Excel rows. The level legend is in D2, and the rule level is column J.

| Rule | Production row | NT 009 row | Verified requirement |
| --- | --- | --- | --- |
| E0010, level 1 | 147 | 220 | Own-application series are 00001–49999. Mobile uses 50000–69999; web 70000–79999; manual web transcription 80000–89999. |
| E0015, level 1 | 150 | 223 | Competence must be no later than emission. The builder compares against the same Brasília timestamp serialized as dhEmi. |
| E0580, level 2 | 493 | 638 | Immunity, service export and non-incidence (tribISSQN 2/3/4) forbid ISS withholding. |
| E0588, level 2 | 495 | 640 | A special tax regime forbids ISS withholding. Production lists regimes 1–6 and 9; NT 009 lists 1–6. |
| E0617, level 3 | 509 | 657 | pAliq is forbidden for a non-optante when the ISS-incidence municipality agreement is active at competence. |
| E0625, level 3 | 512 | 660 | pAliq is forbidden when all five conditions hold: ME/EPP, ISS assessed through SN, active incidence-municipality agreement at competence, no exemption/differentiated-rate municipal benefit, and no ISS withholding. |
| E0621, level 3 | 511 | 659 | Under the same active-agreement/no-exception benefit conditions as E0625, withholding (2 or 3) requires pAliq, with a minimum of 1.80%. |
| E0628, level 3 | 513 | 661 | ME/EPP with ISS assessed through SN and withholding (2 or 3) requires pAliq of at least 1.80% when the incidence municipality's agreement is not active. |
| E0631, level 3 | 514 | 662 | ME/EPP with ISS assessed through SN must omit pAliq without withholding when the incidence municipality's agreement is not active. |

Level 3 rules depend on municipal legislation configured in the national system,
but complementary rules can cover both active and inactive agreements. The
previous analysis of E0625 alone missed E0631. In the current builder there is
no BM group, so no exemption/differentiated-rate benefit is supplied. Together,
E0625/E0631 forbid a rate without withholding for ME/EPP with regApTribSN=1;
E0621/E0628 require it with withholding, with a 1.80% minimum. The builder now
guards these combinations without guessing agreement status. Any future BM
support must revisit the active-agreement benefit exceptions. E0617 for
non-optantes remains dependent on municipal configuration. Caller input is
rejected when invalid, never silently stripped. The series restriction belongs
to own-application construction, not structural Id lookup.

Within the new ME/EPP/SN-assessed withholding branch, E0580/E0588 are checked
before requiring a rate. Otherwise the error would advise adding a rate to a
document whose withholding is itself forbidden. This precedence fix does not
extend the new rate policy to other assessment regimes or non-optantes.

## Midnight timestamp boundary

The default emission timestamp is backdated by five seconds. A current-day
competence can therefore follow that timestamp's Brasília date during the first
five seconds after midnight. E0015 deliberately fails closed in that window;
schedule after it or retry rather than silently changing the fiscal competence
or removing the clock-skew margin. Regression coverage simulates both sides of
the boundary without making network requests.

## NT 009 migration boundary

The October 29 notice identifies NT 004 plus the NT 007 retention-domain changes
as current production. It identifies NT 009 v1.0 release 1 as available for tests,
with later releases to enter testing first. Releases 1–3 are targeted for
2026-12-01 production, while a formal schedule remains to be published.
This is an announced target, not proof of the exact rejection date for old XML.

The bundled 2026-10-20 test schemas implement release 1 only. NT 009 v1.01 adds
opSimpNac=4, regApIBSCBSSN and cAtvSN and relocates indDest/dest to infDPS.
Those changes are not implemented here. A follow-up needs the corresponding
official XSD bundle and explicit versioned layout support; changing the date or
pointing production at the existing NT009 enum does not implement that migration.
On this verification date, the [official test documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita)
still links the 2026-10-20 XSD bundle; no release-2 XSD bundle was linked there.

The notice permits ADN to receive missing IBS/CBS groups even for obligated
issuers. Supplying the group requires its applicable mandatory data, including
recipient information when required by the operation. The current model is
partial. No issuer's compliance, payment waiver or 2027 tax treatment was verified.
