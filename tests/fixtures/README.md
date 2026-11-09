# Test fixtures

`pynfsenacional_golden_dps_signed.xml` comes from
[Gomes343/PyNFSeNacional](https://github.com/Gomes343/PyNFSeNacional) (MIT), file
`tests/golden/gabarito_dps_assinado.xml`. That project generated it with a private PHP reference
signer and a throwaway self-signed certificate. We use it as an interoperability oracle: our
verifier must accept a signature that another implementation produced.
