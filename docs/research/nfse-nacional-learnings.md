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
| E0617, level 3 | 509 | 657 | pAliq is forbidden for a non-optante when the ISS-incidence municipality agreement is active at competence. |
| E0625, level 3 | 512 | 660 | pAliq is forbidden when all five conditions hold: ME/EPP, ISS assessed through SN, active incidence-municipality agreement at competence, no exemption/differentiated-rate municipal benefit, and no ISS withholding. |

Level 3 rules depend on municipal legislation configured in the national system.
The current builder has neither agreement-status nor benefit context and must
not invent it. Explicit pAliq remains preserved; the example omits it. The series
restriction belongs to own-application construction, not structural Id lookup.

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
