# Revisão final <ID da feature>

revisor: reviewer

## Critérios de aceite
- [x] A1 — <evidência: arquivo:linhas ou comando>

## Gates
| Gate | Resultado |
|---|---|
| `pnpm gate tests-reviewed <feature>` | PASS/FAIL |
| `pnpm gate coverage <feature>` | PASS/FAIL |
| `pnpm gate mutation <feature>` | PASS/FAIL (score por arquivo de domínio) |
| `pnpm gate e2e <feature>` | PASS/FAIL |
| `pnpm verify` | verde/vermelho |

## Diff
- Escopo: <só arquivos do plano?>
- Achados: <arquivo:linha e o problema; "nenhum" se zero>

## Lacunas
- <cenário do contrato sem teste, comportamento sem cenário; "nenhuma" se zero>

## Defeitos abertos
Achado não corrigido nesta revisão (inclusive "baixa" ou "risco aceito") vira defeito
([workflow defeito](../defeito.md)).
- <D<n>: achado de origem; "nenhum" se zero>

## Veredito
<aprovado | reprovado>
