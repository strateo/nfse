"""Environments, base URLs and the DPS layout each one currently expects."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Layout(enum.Enum):
    """DPS layout generation. Both declare ``versao="1.01"`` but differ in structure.

    V1_01: production layout (XSD bundle 2026-02-09 + alphanumeric-CNPJ patterns).
    NT009_R1: NT 009 v1.0 release 1, the 2026-10-20 Produção Restrita bundle.
    NT009 remains its compatibility name; neither name selects later releases.
    """

    V1_01 = "v1.01"
    NT009 = "nt009"
    NT009_R1 = NT009

    @property
    def release(self) -> str:
        """Exact bundled release identity, independent of environment rollout dates."""
        return {
            Layout.V1_01: "v1.01-20260209",
            Layout.NT009_R1: "nt009-r1-20261020",
        }[self]


@dataclass(frozen=True)
class Environment:
    name: str
    tp_amb: str
    sefin_url: str
    adn_url: str
    parametros_url: str
    layout: Layout


PRODUCAO = Environment(
    name="producao",
    tp_amb="1",
    sefin_url="https://sefin.nfse.gov.br/SefinNacional",
    adn_url="https://adn.nfse.gov.br/contribuintes",
    parametros_url="https://adn.nfse.gov.br/parametrizacao",
    layout=Layout.V1_01,
)

PRODUCAO_RESTRITA = Environment(
    name="producao_restrita",
    tp_amb="2",
    sefin_url="https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
    adn_url="https://adn.producaorestrita.nfse.gov.br/contribuintes",
    parametros_url="https://adn.producaorestrita.nfse.gov.br/parametrizacao",
    layout=Layout.NT009_R1,
)
