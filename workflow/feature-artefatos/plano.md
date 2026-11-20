# Plano <ID da feature>: <título>

## Escopo
<o que entra; o que fica de fora; arquivos criados ou alterados>

Cartões: `<arquivo dos cartões da feature e IDs, ou "nenhum">`

## Contrato
Vem antes da regra de negócio; o primeiro cenário prova o contrato
([princípios](../../capability/testing.md#princípios)).

- `<arquivo>`: `<assinatura exata, schema ou payload>`

## Arquivos de produção
Os `tdd` viram `sources` no [estado](estado.md); os `tdd` marcados como domínio viram `domainSources` (gate
[mutacao](../../gate/mutacao.md)); os dispensados viram `exemptSources`. Domínio = regra de negócio ou cálculo;
tela, adaptador e cola não são domínio. Classificação e regras da dispensa:
[testing](../../capability/testing.md#classificação-tdd).

| Arquivo | Domínio | Teste | Motivo |
|---|---|---|---|
| `<caminho do arquivo de produção>` | sim/não | tdd / dispensa-estilo / dispensa-biblioteca | <obrigatório na dispensa; "-" no tdd> |

## Domínio das entradas
Uma linha por entrada externa (campo de tela, parâmetro de API, arquivo lido, variável de ambiente). Texto vago
("valor válido", "número positivo") não serve: diga o formato e os limites. Sem fonte para um limite = pendência.

| Entrada | Formato aceito | Mínimo | Máximo | Exemplos rejeitados | Erro esperado | Fonte |
|---|---|---|---|---|---|---|
| `<campo>` | <ex.: só dígitos, sem espaço> | <ex.: 1> | <ex.: 360> | <ex.: "", " 6 ", "1e1", "6.5", "-1", "0", "361"> | <classe ou mensagem> | <RF/RN/RFC §> |

## Riscos
- <risco e como o teste o cobre>

## Dependências
- <feature, regra ou arquivo de que depende>

## Critérios de aceite
Molde fixo, um bloco por critério. `camada` é onde o critério é provado: `unit`, `prop` (propriedade), `e2e` ou
`mut` (mutação); pode haver mais de uma (ex.: `unit, prop`). Regras de camada: [testing](../../capability/testing.md).

### A1: <título curto>
- entrada: <campos e valores, ou a classe de valores da tabela de domínio>
- domínio: <entradas da tabela que o critério usa>
- saída: <valor exato esperado>
- erro: <erro esperado para entrada fora do domínio, ou "nenhum">
- fonte: <RF/RN/RFC §>
- camada: <unit | prop | e2e | mut>

## Cenários
- C1 (A1, unit): <entrada e valor esperado exato> (fonte <RF/RN/RFC §>)
- C2 (A1, unit): <entrada rejeitada da tabela de domínio e o erro esperado>
- C3 (A2, prop): <propriedade que vale para todo valor do domínio>

## Arquivos de teste previstos
- `<caminho do *.test.ts>` (pacote `<pnpm --filter>`; camadas unit, prop)
- `<caminho do teste e2e>` (camada e2e; "nenhum" se não há critério e2e)

## Pendências
- <pergunta sem resposta nos documentos; "nenhuma" se zero>
