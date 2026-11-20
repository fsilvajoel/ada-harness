# Capability: testing

## Objetivo
Definir e validar o comportamento esperado em testes antes de a implementação ser aceita.

## Composição
- skills: `escrever-teste`,
  `revisar-teste`
- papéis: `test-writer`, `reviewer`
- policy: [quality](../policy/quality.md)
- gates: [expect-red](../gate/expect-red.md), [testes-revisados](../gate/testes-revisados.md),
  [cobertura](../gate/cobertura.md), [mutacao](../gate/mutacao.md), [e2e](../gate/e2e.md),
  [auditoria](../gate/auditoria.md)
- artefatos: [testes.md](../workflow/feature-artefatos/testes.md), [auditoria.md](../workflow/feature-artefatos/auditoria.md)

## Princípios
- **Contrato primeiro.** Toda feature começa pelo contrato (assinatura, schema ou payload, na seção Contrato do
  plano) e pelo teste que prova o contrato. A regra de negócio vem depois. Mudar payload sem atualizar o contrato
  e o teste do contrato é FAIL na revisão.
- **Comportamento, não estrutura.** O teste verifica o que o usuário ou quem chama observa (saída, erro, texto,
  estado visível). Não verifica classe CSS, estrutura de componente, nome de função interna ou ordem de chamadas.
- **Lógica isolada.** Regra, cálculo, validação e exibição condicional saem da tela para uma função, hook ou
  schema que se testa sem renderizar layout. A tela só monta.

## Classificação TDD
Cada arquivo de produção do plano recebe uma classificação, com motivo. O gate lê a classificação do estado;
ninguém decide fora do artefato.

| Classificação | Quando | Efeito |
|---|---|---|
| `tdd` | regra, cálculo, validação, formulário, exibição condicional, contrato, adaptador | teste antes; entra em `sources` (e em `domainSources` se for domínio); expect-red, cobertura e mutação valem |
| `dispensa-estilo` | só layout e estilo, sem condição nem cálculo | vai para `exemptSources` com motivo; revisão visual no navegador |
| `dispensa-biblioteca` | componente base copiado ou gerado de biblioteca já testada, sem alteração de comportamento | vai para `exemptSources` com motivo |

Regras da dispensa:
- arquivo com `if`, cálculo, validação ou estado que muda a saída não é dispensável: extraia a lógica para um
  arquivo `tdd` e dispense só o que sobra;
- componente de biblioteca alterado (prop nova, comportamento novo) volta a `tdd`;
- toda feature tem ao menos um arquivo `tdd`;
- um fluxo crítico do PRD tem `e2e` mesmo quando as telas dele são dispensadas;
- o `reviewer` confere cada motivo; motivo vago ("é simples", "é só tela") é FAIL.

Ordem na interface: montar a tela com os componentes base e conferir no navegador; extrair a lógica; escrever o
teste de comportamento da lógica antes de implementá-la (expect-red vale para ela).

## Checklist de bordas
Para cada entrada em texto ou número da tabela de domínio do plano, o teste cobre (ou o plano justifica por que
não se aplica):

- [ ] vazio (`""`)
- [ ] só espaços (`"   "`)
- [ ] espaços em volta (`" 6 "`)
- [ ] notação exponencial (`"1e1"`)
- [ ] separador decimal ou de milhar errado (`"6.5"` onde se espera vírgula, `"1.000,00"` onde não há milhar)
- [ ] negativo
- [ ] zero
- [ ] limite (mínimo e máximo do domínio)
- [ ] limite + 1 (logo fora do mínimo e do máximo)
- [ ] muito grande (além do inteiro seguro da linguagem, texto longo)
- [ ] caractere inválido (letra, símbolo, caractere de controle)
- [ ] caractere não ASCII em texto ordenado ou paginado (ex.: sigma final `Σ`, `İ`, acento): a chave de ordenação que o
      cursor guarda vem do próprio banco, e o teste pagina de 1 em 1 conferindo que ninguém repete nem some

Cada entrada do plano tem ao menos um caso de rejeição com o erro esperado exato.

## Camadas
| Camada | Quando é obrigatória | Como se prova |
|---|---|---|
| `unit` | todo critério com valor exato | teste unitário com asserção exata |
| `prop` | parser e cálculo | teste de propriedade (biblioteca de geração de casos, do tipo fast-check) sobre o domínio inteiro, além dos exemplos |
| `e2e` | fluxo de usuário descrito no PRD | teste de ponta a ponta no navegador (Playwright ou equivalente), listado em `e2e` do estado; gate [e2e](../gate/e2e.md) |
| `mut` | arquivo de domínio (regra de negócio, cálculo) | score de mutação acima do mínimo do projeto; gate [mutacao](../gate/mutacao.md) |

Cobertura de 100% não prova domínio: ela mostra que a linha rodou, não que a entrada errada foi recusada.
