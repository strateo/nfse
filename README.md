# NFS-e Nacional

<p align="center">
  <img src="assets/icon.png" width="128" alt="Ícone do NFS-e Nacional">
</p>

<p align="center">
  Cliente Python para emissão, consulta e cancelamento de NFS-e no Padrão Nacional.
</p>

<p align="center">
  <a href="https://github.com/strateo/nfse/actions/workflows/tests.yml"><img src="https://github.com/strateo/nfse/actions/workflows/tests.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/strateo/nfse/releases/tag/v0.1.4"><img src="https://img.shields.io/badge/release-v0.1.4-104A58" alt="v0.1.4"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-104A58" alt="MIT"></a>
</p>

<p align="center">
  <strong>Português (Brasil)</strong> · <a href="README.en-US.md">English (US)</a>
</p>

Cliente Python 3.11+ para o Padrão Nacional da NFS-e, com emissão pela SEFIN Nacional, cancelamento, consultas e distribuição de DF-e pelo ADN.

Software independente, em estágio inicial de desenvolvimento. Os testes locais não comprovam aceitação pela SEFIN, prontidão para produção nem o enquadramento tributário de um emissor. Consulte a [documentação oficial](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica) para os requisitos vigentes.

Dependências: `lxml`, `cryptography` e `reportlab` (PDF do DANFSe). O transporte HTTP usa a biblioteca padrão do Python.

## Recursos

- Construção de DPS nos leiautes de produção e Produção Restrita.
- Certificado A1 (leitura do CNPJ do titular do e-CNPJ e verificação de validade e de uso da chave), assinatura XML e validação pelos XSDs oficiais.
- Emissão, recuperação, consulta e cancelamento pela SEFIN; distribuição pelo ADN.
- Geração local do DANFSe em PDF, com fontes de fallback Unicode explícitas.
- Valores em `Decimal` e verificações locais das regras de preenchimento.

## Início rápido

Clone o repositório e instale a biblioteca em um ambiente virtual:

```sh
git clone https://github.com/strateo/nfse.git
cd nfse
python -m venv .venv
.venv/Scripts/python -m pip install .
```

No Linux e no macOS, use `.venv/bin/python`. É necessário Python 3.11 ou superior.

A biblioteca não é um serviço hospedado de emissão. Sua aplicação deve persistir os documentos e controlar a numeração das DPS.

## Limites dos esquemas para CNPJ alfanumérico

A validação de CPF/CNPJ e o campo CNPJ do tomador aceitam CNPJ alfanumérico,
mas isso não garante suporte completo à emissão por um prestador com letras no CNPJ.
O pacote de produção de 09/02/2026 mantém padrões numéricos para os identificadores
de DPS, NFS-e e eventos. No pacote de Produção Restrita de 20/10/2026, o padrão
de `chNFSe` também diverge da posição do CNPJ nos identificadores relacionados.
Esses casos podem falhar na validação XSD local; não são comprovadamente aceitos
pela SEFIN. Os esquemas oficiais são preservados, sem ampliar esses padrões por
suposição. Confira os pacotes na [documentação oficial](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual).

## Uso

`build_dps` emite pelo canal de aplicativo próprio: use série de 1 a 49999
(E0010). A competência não pode ser posterior à data de emissão em Brasília
(E0015). Os identificadores de outros canais continuam disponíveis em `dps_id`.

O exemplo omite `pAliq`. Para ME/EPP com ISS apurado pelo Simples
(`regApTribSN=1`), o modelo atual, que não oferece o grupo de benefício municipal
`BM`, exige omitir a alíquota sem retenção (E0625/E0631). Com retenção
(`tpRetISSQN=2` ou `3`), exige uma alíquota entre 1,80% e 5,00% (E0621/E0628 e
E0595). Valores inválidos são rejeitados, nunca removidos silenciosamente.
As demais regras municipais, como E0617 para não optantes, ainda exigem consultar
a parametrização do município; a validação local não garante aceitação pela SEFIN.

`totTrib` segue o regime do prestador: MEI não pode enviar `pTotTribSN` (E0710),
ME/EPP não pode enviar `indTotTrib` (E0712) e Não Optante não pode enviar nenhum
dos dois (E0713), restando `TotalTax.percent()` ou `TotalTax.amount()`. O endereço
do tomador identificado por CNPJ é opcional: a SEFIN não executa a E0235 na DPS
enviada pelo prestador.

Por padrão, `dhEmi` é retroagido em cinco segundos para compensar diferença de
relógio com a SEFIN. Nos primeiros cinco segundos após a meia-noite em Brasília,
isso pode cair no dia anterior e rejeitar `dCompet` do dia atual (E0015).
Agende a emissão após essa janela ou tente novamente; a biblioteca não altera
silenciosamente a competência nem remove a margem de segurança.

O exemplo usa dados fictícios e o ambiente de Produção Restrita. Implemente `next_number()` com um contador atômico por CNPJ e série e `save_pending()` com armazenamento durável antes de enviar documentos. Os nomes e comentários de código são mantidos iguais nas duas versões deste README.

```python
from datetime import date
from decimal import Decimal

from nfse_nacional import dps as d, schemas, signing
from nfse_nacional.certificate import Certificate
from nfse_nacional.client import SefinClient
from nfse_nacional.environment import PRODUCAO_RESTRITA as ENV
from nfse_nacional.errors import RejectionError, TransportError

cert = Certificate.from_pfx_file("empresa.pfx", "senha")
cert.check(cnpj="11222333000181")  # e-CNPJ holder, validity and key usage
sefin = SefinClient(cert.ssl_context(), ENV)

note = d.Dps(
    cLocEmi="3550308", serie="900", nDPS=next_number(),  # atomic counter per (CNPJ, serie)
    dCompet=date(2026, 11, 3),
    prest=d.Provider(opSimpNac=d.OP_SIMP_NAC_ME_EPP, CNPJ="11222333000181", regApTribSN="1"),
    serv=d.Service(cTribNac="010101", xDescServ="Consultoria em TI", cLocPrestacao="3550308"),
    vServ=Decimal("1500.00"),
    tribMun=d.MunicipalTax(),
    totTrib=d.TotalTax.simples(Decimal("6.00")),
    toma=d.Taker(xNome="Acme Ltda", CNPJ="11444777000161",
                 end=d.Address(xLgr="Av. Paulista", nro="1000", xBairro="Bela Vista",
                               cMun="3550308", CEP="01310100")),
)

root = d.build_dps(note, ENV)          # offline rule checks
signing.sign(root, cert)
schemas.validate(root, ENV.layout)     # official XSD for this environment
xml = signing.to_bytes(root)
save_pending(note.id, xml)             # persist BEFORE sending

try:
    result = sefin.emit(xml)
except TransportError:
    result = sefin.recover(xml, note.id)  # same XML, never a new nDPS
except RejectionError as e:
    print(e.codes)                         # e.g. ['E0712']
```

Para cancelar, use `events.build_cancellation(...)`, assine com `signing.sign` e envie com `sefin.register_event(key, xml)`. Para distribuição, chame `AdnClient(cert.ssl_context(), ENV).fetch_dfe(last_nsu)` até `batch.exhausted`, persistindo `batch.last_nsu` após cada lote somente quando ele não for `None`.

### Rejeições comuns

| Resposta | Significado | O que fazer |
| --- | --- | --- |
| HTTP 403 com corpo HTML | A SEFIN recusou ou não recebeu o certificado do cliente. | Verifique o certificado com `cert.check()` e use `cert.ssl_context()` sem alterações. |
| E0008 | `dhEmi` é posterior ao horário de processamento da SEFIN. | Mantenha o `dhEmi` padrão e corrija o relógio da máquina. |
| E0014 | O identificador da DPS já gerou uma NFS-e. | Chame `sefin.recover(xml, note.id)` com o XML salvo; nunca renumere antes. |
| E0015 | `dCompet` é posterior à data de emissão. | Tente novamente após os primeiros segundos da meia-noite em Brasília. |
| E0116 | O município tem cadastro CNC do prestador e `IM` não foi informada. | Envie `Provider.IM`. |
| E0120 | `IM` foi informada, mas o município não tem cadastro CNC do prestador. | Omita `Provider.IM`. |
| E0710, E0712, E0713 | A opção de `totTrib` não corresponde ao regime. | Use o construtor de `TotalTax` permitido para o regime. |

### DANFSe (PDF)

O PDF é gerado localmente em uma página A4, seguindo como referência a NT 008/2026 v1.02:

```python
from nfse_nacional.danfse import Watermark, render_danfse

pdf = render_danfse(result.nfse_xml)                       # authorized NFS-e XML from emit()
pdf = render_danfse(result.nfse_xml, watermark=Watermark.CANCELLED)  # after a 101101 cancellation event
```

Cancelamento e substituição são eventos separados do XML da NFS-e; informe a marca-d'água correspondente. Em Produção Restrita, o documento recebe automaticamente a indicação “NFS-e SEM VALIDADE JURÍDICA”.

## Teste de integração em Produção Restrita

O script `scripts/smoke_producao_restrita.py` executa o ciclo com um certificado A1 real:

- Verifica o certificado e a conexão mTLS com o ADN.
- Consulta `HEAD /dps`.
- Constrói, valida e assina a DPS.
- Emite a nota, gera o DANFSe e consulta o documento.
- Com `--cancel`, cancela a nota.

O script se limita à Produção Restrita, cujas notas não têm validade jurídica. A senha do PFX vem de `NFSE_PFX_PASSWORD` ou de uma entrada oculta no terminal.

```bash
.venv/Scripts/python scripts/smoke_producao_restrita.py --pfx empresa.pfx --c-loc-emi 3550308 --op-simp-nac 3 --p-tot-trib-sn 6.00 --c-trib-nac 010101 --toma-cnpj 11444777000161 --toma-street "Av. Paulista" --toma-number 1000 --toma-district "Bela Vista" --toma-c-mun 3550308 --toma-cep 01310100 --cancel
```

Os documentos, o `report.json` com os códigos de erro de cada etapa e o estado da numeração são gravados em `smoke-output/`. Cada documento assinado é salvo antes do envio. Use `--dry-run` para parar antes de qualquer chamada de rede e `--profile RSA_SHA256` para testar o perfil de assinatura alternativo.

Use `--check-recovery` para conferir a recuperação da mesma DPS salva. Com
`--cancel`, o script também consulta o evento registrado. A distribuição pelo ADN
tem limite de lotes e distingue progressão do NSU de fila esgotada. O arquivo
`acceptance-summary.json` registra resultados sem identificadores, XML ou textos
livres do servidor; os demais arquivos e os logs continuam privados. Confira o
[procedimento de aceitação e os leiautes suportados](docs/acceptance.md) (em inglês).

O contador desse script atende testes com um único operador; ele não é um alocador concorrente para produção. Uma execução real com A1 exige autorização do emissor. A validação XSD local não comprova consistência de CEP, poderes de representação nem classificação tributária. Os testes automatizados do script usam certificados temporários e param antes da rede.

## Desenvolvimento

### Transição de leiaute em produção

Situação verificada em 09/11/2026: o comunicado oficial de 29/10 mantém NT 004/007
em produção e prevê os releases 1 a 3 da NT 009 em produção em 01/12/2026, com
implantação gradual e cronograma formal a publicar. `PRODUCAO` ainda usa o leiaute
anterior, e `Layout.NT009_R1` (também disponível pelo nome `Layout.NT009`)
representa somente o release 1 de testes. `environment.layout.release` identifica
o pacote exato incluído. Esta versão
não implementa os ajustes posteriores da NT 009 v1.01 nem muda de leiaute pela data.
Uma implantação em produção exige conferir o cronograma e o pacote oficial
correspondente; a compatibilidade após a transição não está comprovada.

O ADN pode aceitar a ausência de IBS/CBS mesmo para emissores sujeitos à obrigação.
Informar o grupo exige cumprir os campos e classificações aplicáveis; o modelo
atual é parcial e a aceitação técnica não comprova conformidade tributária.
Veja o [comunicado oficial](https://www.gov.br/nfse/pt-br/noticias/nfs-e-nacional-confira-oas-rientacoes-sobre-leiaute-em-producao-regras-de-preenchimento-de-ibs-cbs-e-status-da-nota-tecnica-009)
e as [regras verificadas](docs/research/nfse-nacional-learnings.md).

### Testes locais

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -W error::ResourceWarning -m unittest discover -s tests
```

No Linux e no macOS, substitua `.venv/Scripts/python` por `.venv/bin/python`. Os testes usam documentos sintéticos, certificados temporários e servidores mTLS locais, sem acessar serviços governamentais. Três testes de fontes são ignorados em sistemas sem Malgun Gothic e Arial; o CI do Windows cobre esses casos.

Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para contribuir, [CHANGELOG.md](CHANGELOG.md) para o histórico e [PUBLICATION.md](PUBLICATION.md) para o processo de publicação. Esses documentos de apoio estão em inglês.

## Licença

O código do projeto é distribuído sob a [licença MIT](LICENSE). O material de terceiros mantém seus próprios termos; veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
