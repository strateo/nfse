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
| E0235, level 1 | 259 | 350 | A CNPJ recipient needs a national address, but column K marks the rule as not executed when SEFIN receives a provider DPS. It runs when the ADN receives an NFS-e shared by a municipality. The builder does not enforce it. |
| E0242, level 1 | 264 | 359 | A foreign recipient needs a foreign address (`endExt`); column K is V in both sheets. Production requires it for a NIF recipient when the issuer is identified by CNPJ. NT 009 requires it for NIF or cNaoNIF, whatever the issuer. The builder follows each layout. The intermediary rule (E0298) is unchanged in NT 009, and NT 009's `dest` rule E0923 is not modelled. |
| E0580, level 2 | 493 | 638 | Immunity, service export and non-incidence (tribISSQN 2/3/4) forbid ISS withholding. |
| E0588, level 2 | 495 | 640 | A special tax regime forbids ISS withholding. Production lists regimes 1–6 and 9; NT 009 lists 1–6. |
| E0617, level 3 | 509 | 657 | pAliq is forbidden for a non-optante when the ISS-incidence municipality agreement is active at competence. |
| E0625, level 3 | 512 | 660 | pAliq is forbidden when all five conditions hold: ME/EPP, ISS assessed through SN, active incidence-municipality agreement at competence, no exemption/differentiated-rate municipal benefit, and no ISS withholding. |
| E0621, level 3 | 511 | 659 | Under the same active-agreement/no-exception benefit conditions as E0625, withholding (2 or 3) requires pAliq, with a minimum of 1.80%. |
| E0628, level 3 | 513 | 661 | ME/EPP with ISS assessed through SN and withholding (2 or 3) requires pAliq of at least 1.80% when the incidence municipality's agreement is not active. |
| E0631, level 3 | 514 | 662 | ME/EPP with ISS assessed through SN must omit pAliq without withholding when the incidence municipality's agreement is not active. |
| E0710, level 1 | 544 | 692 | MEI must not send pTotTribSN. |
| E0712, level 1 | 541 | 689 | ME/EPP must not send indTotTrib. |
| E0713, level 1 | 542 | 690 | Não Optante must send neither indTotTrib nor pTotTribSN, leaving vTotTrib or pTotTrib. |

Column K of the rule sheet says whether a rule runs when SEFIN receives a DPS
sent by the provider (V) or not (X); row 2 holds the column legends. Offline
guards follow column K. The totTrib rules above are the only regime rules on
that group in either sheet, so vTotTrib and pTotTrib stay open to every regime.

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

## TLS behaviour of the endpoints

Observed on 2026-11-12 with handshakes that carried no client certificate, in
production and Produção Restrita. SEFIN negotiates TLS 1.2 and requests the
client certificate by renegotiating after it reads the HTTP request; without one
it answers an HTML 403. ADN negotiates TLS 1.3 and requests the certificate in
the handshake. The default context from `Certificate.ssl_context()` allows both.
The local mTLS test server requests the certificate in the handshake, so the
renegotiation path is not covered by the test suite, and no authenticated call
was made for this observation.

## Municipal parameters API

The contract was retrieved directly from the official Produção Restrita service
on **2026-11-16 at 01:29:33 UTC**, using an installed A1 certificate for mTLS:

- [Official API directory](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao),
  which links the municipal-parameters documentation in both environments.
- [Official Produção Restrita OpenAPI](https://adn.producaorestrita.nfse.gov.br/parametrizacao/swagger/v1/swagger.json),
  HTTP 200, OpenAPI 3.0.1, title `API NFS-e - ADN Parâmetros Municipais`, version `v1`.
- [Unmodified downloaded contract](adn-parametrizacao-openapi.json), SHA-256
  `fe11afdc72ee27161af5d906206e4f1d422f48e7556525324ca52a72867d3bd5`.
  This is service documentation, with no certificate, credentials or taxpayer data.

The contract's server base is `/parametrizacao`. Its agreement operation is
`GET /{codigoMunicipio}/convenio`, with an integer municipality path parameter
and **no competence parameter**. It has no `/parametros_municipais` segment.
`ParametrosClient.get_convenio` accepts a seven-digit ASCII IBGE code as a string,
uses `Environment.parametros_url`, and returns the JSON object unchanged.
The production base URL is published in the official API directory; the
production Swagger and an actual agreement query were not exercised in this run.
The successful authenticated Swagger download proves access to the documentation,
not live acceptance of the Python client or an issuer's tax treatment.

HTTP 200, 400 and 404 all reference
`ResultadoConsultaConfiguracoesConvenio`, whose properties are nullable string
`mensagem` and object `parametrosConvenio`. The latter references
`ParametrosConfiguracaoConvenio`:

| Property | OpenAPI type / enum |
| --- | --- |
| `tipoConvenioDeserializationSetter` | `TipoConvenio`: integer 1 or 2 |
| `aderenteAmbienteNacional` | `TipoSimNao`: integer 0, 1 or -1 |
| `aderenteEmissorNacional` | `TipoSimNao`: integer 0, 1 or -1 |
| `situacaoEmissaoPadraoContribuintesRFB` | `TipoSituacaoEmissaoPadraoContribuintesRFB`: integer 0, 1 or -1 |
| `aderenteMAN` | `TipoSimNao`: integer 0, 1 or -1 |
| `permiteAproveitametoDeCreditos` | nullable boolean; spelling retained from the contract |

The OpenAPI does not label the enum values or mark properties as required.
The client preserves the wire values and accepts a successful response only when
`parametrosConvenio` is an object. It does not infer missing flags or collapse
0/-1 into booleans. Documented 400/404 answers with a string `mensagem` raise
`RejectionError`, preserving the body and plain message as code `UNKNOWN` because
this schema supplies no E-code. Unusable bodies and other HTTP statuses raise
`TransportError`, retaining the existing Retry-After handling.

The same contract also lists service-rate, rate-history, special-regime,
withholding and benefit queries. This change implements only the agreement
query requested in issue #13. An agreement lookup alone cannot establish an
active agreement at a historical competence date, supply a service rate or
resolve the E0617/E0621/E0625/E0628/E0631 rules. No new offline fiscal guard or
automatic DPS mutation is introduced. Local synthetic mTLS tests cover routing,
raw responses, errors, input validation and the client-certificate requirement;
they do not query government services.

## NFS-e fiscal deadlines under Ato Conjunto 7/2026

The [November 13 CGNFS-e notice](https://www.gov.br/nfse/pt-br/noticias/cgnfs-e-informa-ato-conjunto-rfb-cgibs-no-7-2026-atualiza-o-cronograma-de-obrigatoriedade-de-emissao-da-nfs-e-para-ibs-e-cbs)
summarizes [Ato Conjunto RFB/CGIBS nº 7/2026](https://cgibs.gov.br/upload/arquivos/202611/13061456-ato-conjunto-7-rfb-cgibs-alteracao-ato-4.pdf),
which amended [Ato Conjunto nº 4/2026, art. 1](https://hom.cte.fazenda.gov.br/portal/informe.aspx?informe=108).
The dates below apply to taxable events on or after the stated date for NFS-e
issuance for IBS/CBS purposes.

| NFS-e operation (art. 1, III) | General schedule | Simples Nacional taxpayers covered by § 1 |
| --- | --- | --- |
| **d** — Services subject to ISS on the LC 116/2003 list, excluding items a–c | 2026-10-01 (unchanged) | 2027-01-01 |
| **e–h** — Intangible supplies outside the LC 116/2003 list and outside ICMS, condominium fees and other condominium revenue, rental of movable property, rental, transfer for consideration or leasing of immovable property except where item d applies, and other services outside items a–g | 2027-01-01 | 2027-01-01 |
| **a–c** — Digital platform supplies or intermediation covered by LC 214/2025 art. 22, ISS services under LC 116/2003 subitems 1.03, 1.05 and 1.09, and municipal collective passenger transport under subitem 16.01 | 2027-03-01 | 2027-03-01 |

For Simples Nacional, § 1 sets January 1 for NFS-e items d–h and March 1
for the other cases in art. 1. Issuer integrations must determine the applicable
regime and operation for each taxable event. The DPS field `opSimpNac` alone
cannot establish the deadline. Ordinary NFS-e and ISS requirements remain
separate from this IBS/CBS schedule.

## NT 009 migration boundary

The October 29 notice identifies NT 004 plus the NT 007 retention-domain changes
as current production. It identifies NT 009 v1.0 release 1 as available for tests,
with later releases to enter testing first. The notice targets 2026-12-01 for
technical deployment of releases 1–3 in production, with a formal schedule to
follow. The exact rejection date for old XML remains unconfirmed.

The bundled 2026-10-20 test schemas implement release 1 only. NT 009 v1.01 adds
opSimpNac=4, regApIBSCBSSN and cAtvSN and relocates indDest/dest to infDPS.
Those changes are not implemented here. A follow-up needs the corresponding
official XSD bundle and explicit versioned layout support; changing the date or
pointing production at the existing NT009 enum does not implement that migration.
On this verification date, the [official test documentation](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/producao-restrita)
still links the 2026-10-20 XSD bundle; no release-2 XSD bundle was linked there.

## Official RTC calculators

Checked on 2026-11-25 against the Receita Federal repositories
[calculadora-rtc-regime-regular](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular)
(1.6.1 → [1.7.2](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular/blob/1.7.2/CHANGELOG.md),
released 2026-11-25) and
[calculadora-rtc-simples-nacional](https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional)
(0.0.6 → [1.0.2](https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional/blob/1.0.2/CHANGELOG.md),
released 2026-11-20). The GitHub release notes are empty; the findings come from
each repository's `CHANGELOG.md` and the regime-regular Flyway migrations in
`flyway/sql/manutencao/`. This library emits CST, cClassTrib and cIndOp but
computes no IBS/CBS amounts, so none of these releases changes its behaviour.

| Upstream change | Consequence for this library's callers |
| --- | --- |
| V0065 moves the `CLASSIF_NBS_INDOP_LC` and `INDICADOR_OPERACAO_IBS_CBS` switch: the old rows end 2026-11-30 and the new rows start **2026-12-01** (previously 11-02/11-03). V0061 reloads those new rows from the updated Anexo VIII and adds LC 116 item 99.05.01 (condominium operations); V0064 adds the omitted service 20.02, NBS `106062000`, cIndOp `100301`; V0066 removes an invalid row. | The cIndOp set changes on the NT 009 production target date. `IbsCbs.cIndOp` is caller input and is not checked offline, so callers must re-derive it for competences from 2026-12-01. |
| V0062 and V0073 load the cClassTrib table of IT 2025.002 v1.70 (2026-10-01: nine new codes, adjusted classifications and DF-e links) and v1.71 (renames 410003 and 410026, adds indDIR to 410003 and 410999). | `IbsCbs.cClassTrib` is also unchecked; callers must use the current table. |
| Simples Nacional 1.0.0 with regime-regular V0071 calculate Simples Nacional for NF-e and NFS-e in one contract. For the NFS-e the regime comes from `opSimpNac`, `regApIBSCBSSN` and `regApTribSN` (Anexo VI v1.04.01 fields 151–153); the rate used has 2 decimal places (`pIBSSN`/`pCBSSN`); one item; Imposto Seletivo is forbidden (VSN-035). The migration also covers the tpRBSN effect, ISS-in-Simples indicators per activity and the ISS situations that pick the rate pair. | These are the release-2 fields listed as not implemented in the NT 009 boundary above. The calculator's validation (NFS-e rules 291, 292 and 313) is a reference for that follow-up, not a substitute for the official XSD. |
| V0061 (1.6.2) and V0068 (1.7.0) swap the Fator R annexes of activity `cAtivSN 09`: III without and V with Fator R becomes V without and III with, per LC 123/2006 art. 18 §§ 5-J and 5-M. | Relevant once `cAtvSN` is modelled. |
| Regime-regular 1.7.2 rounds each published value once and reuses it: `vIBS` is `vIBSUF + vIBSMun`, totals sum the published item values, the effective rate is rounded to 4 places before multiplying, `vBC` to 2 places, ties go to even. Previously totals could miss the item sum (rejection 1091). | No effect today. `format_decimal` never rounds: it rejects more than two decimal places. Any future IBS/CBS amount calculation must round explicitly by the same policy before serialization. |
| Both moved to Java 25 and Spring Boot 4.1.2 (1.7.0/1.0.0), added the JVM flag `-XX:-UseBASE64Intrinsics` for the 2026-11-17 OpenJDK advisory (1.7.1/1.0.1), and regime-regular 1.7.2 fixes `GET versao/status`, which returned 500 in the offline profile since 1.7.0. | Only relevant when running the offline packages; use 1.7.2 or later. |

No client's cIndOp or cClassTrib values were compared against the new tables.

## Technical validation of IBS/CBS

The October 29 notice permits ADN to receive missing IBS/CBS groups even for obligated
issuers. Supplying the group requires its applicable mandatory data, including
recipient information when required by the operation. The current model is
partial. No issuer's compliance, payment waiver or 2027 tax treatment was verified.
