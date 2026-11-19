# Workflow: feature

## Objetivo
Entregar uma feature numa sequência controlada em que **os testes revisados decidem o pronto** e a implementação
só começa depois deles.

## Quem conduz
O **orquestrador** é qualquer agente de IA. Ele segue esta lista, chama um [papel](../agent/README.md) por etapa
(como, depende do [adaptador](../adapter/README.md)), roda os gates e grava o estado. Os papéis não chamam uns
aos outros.

| Papel | Etapas |
|---|---|
| `planner` | plan |
| `refiner` | refinamento funcional (escreve só no plano, nos cartões da feature e na fila de PM) |
| `test-writer` | tests |
| `reviewer` | revisão dos testes, revisão final (somente leitura) |
| `implementer` | implement |

## Pasta da feature
Tudo da feature fica em `docs/plano/<feature>/` (moldes em [feature-artefatos/](feature-artefatos/README.md)):
`plano.md`, `testes.md`, `revisao.md` (refinamento funcional, revisão dos testes e final) e `estado.json` ([formato](feature-artefatos/estado.md)).

## Lista do orquestrador
Marque cada item na ordem. Gate FAIL ou SKIP bloqueia o item seguinte; a mensagem do gate diz o que falta.

### 1. plan
- [ ] Chamar **planner** com o ID da feature.
- [ ] Conferir `plano.md` pelas [regras do plano](../capability/planning.md#regras-do-plano): escopo, contrato
      (arquivo + assinatura), arquivos de produção com a marca de domínio, tabela de domínio de toda entrada
      externa (formato, mínimo, máximo, exemplos rejeitados, erro esperado, fonte), todo `A<n>` no molde fixo com
      `camada`, todo `C<n>` com valor esperado de fonte citada e ao menos uma rejeição por entrada, arquivos de
      teste previstos. Critério vago ou pendência (inclusive no domínio) = parar e perguntar.
- [ ] Criar o **esboço** do contrato (assinaturas que lançam `Error("esboço: não implementado")`) e excluí-lo
      da cobertura enquanto não for implementado.
- [ ] Criar `estado.json` com `feature`, `sources`, `domainSources`, `criteria` (`A<n>` → camadas), `tests`, `e2e`
      (caminhos dos testes previstos; `[]` se não há critério e2e) e `stages.plan = done`.

### 1b. refinamento funcional
- [ ] Chamar **refiner** com `plano.md`, `estado.json`, os cartões que o plano cita e a fila de pendências de PM
      (`pm_queue` do `harness.project.yaml`). Com `roles.refiner.worker`, a etapa vai para esse trabalhador em
      modo escrita restrita ([cli-agent](../adapter/cli-agent.md)).
- [ ] O refiner confere: (a) requisitos completos para escrever os testes e implementar: todo `A<n>` observável,
      regras com fonte, entradas e saídas exatas, erros, dependências e contrato; (b) todo cartão com DoR (o que
      precisa existir antes: dependências, arquivos a ler, caminhos permitidos) e DoD (como se prova pronto: itens
      e checks) claros, verificáveis e coerentes com o plano.
- [ ] O que não depende de decisão de produto ele **completa sozinho**, no plano e nos cartões da feature:
      critério faltante, caso de erro que os docs aprovados já definem, DoR/DoD incompletos, `refs`, arquivos a
      ler, checks. Sempre com fonte num doc aprovado; regra de negócio nova nunca é inventada.
- [ ] O que depende do PM vira entrada na fila `pm_queue` (cartão, feature, pergunta, opções, sugestão padrão,
      data, `status: aberta`).
- [ ] Veredito: `pronto` (nada mudou), `ajustado` (editou; lista as mudanças) ou `pendencia-pm` (abriu entrada na
      fila; pode ter ajustado o resto).
- [ ] Conferir o diff: só podem mudar o `plano.md` da feature, os cartões dela (os demais cartões do arquivo ficam
      idênticos) e a fila, nesta só entradas dos cartões da feature. Qualquer outra mudança = tentativa rejeitada
      e nada é aplicado.
- [ ] Gravar o parecer no início de `revisao.md` ([molde](feature-artefatos/refinamento.md)) e `stages.refinement`
      no estado, com `verdict` e `round`. Com `ajustado`, atualizar `sources`, `domainSources`, `criteria`,
      `tests` e `e2e` do estado se o plano mudou essas listas, e o esboço se o contrato mudou.
- [ ] `pendencia-pm`: `stages.refinement.status = needs_input` até o PM responder na própria entrada da fila.
      Com a resposta, chamar o refiner de novo (`round` 2): ele aplica a resposta e marca a entrada `resolvida`.
- [ ] No máximo **2 chamadas por feature** (`round` 1 e 2). Se a rodada 2 ainda deixar pendência, a etapa fica
      `needs_input` e o orquestrador conclui com o PM, sem nova chamada.
- [ ] Bloqueio: a etapa 2 só começa com `stages.refinement.verdict` igual a `pronto` ou `ajustado`.

### 2. tests
- [ ] Chamar **test-writer** (skill `escrever-teste`).
- [ ] Gravar em `estado.json` os `scenarios` (`C<n>` → critério, camada, arquivo, teste) e ajustar `tests` e `e2e`
      se a lista mudou. Todo critério tem cenário em cada camada declarada ([testing](../capability/testing.md)).
- [ ] `pnpm gate expect-red <feature>` → PASS (gate [expect-red](../gate/expect-red.md)).

### 3. revisão dos testes
- [ ] Chamar **reviewer** (skill `revisar-teste`); gravar a saída em
      `revisao.md`. Com `roles.reviewer.second_opinion`, rodar também a
      [segunda opinião](../adapter/cli-agent.md#segunda-opinião) (vale igual na etapa 6).
- [ ] Reprovado: voltar à etapa 2 com a lista do reviewer.
- [ ] Aprovado: `pnpm gate tests-reviewed <feature> --sign` (grava `testShas`).

### 4. implement
- [ ] Bloqueio: só começa com `gates.expect-red` e `testShas` gravados no estado.
- [ ] Tirar o esboço da exclusão de cobertura.
- [ ] Chamar **implementer** (skill `editar-arquivo`).
      Com `roles.implementer.worker` no `harness.project.yaml`, a etapa pode ir para esse trabalhador pelo
      adaptador dele ([api-chat](../adapter/api-chat.md) ou [cli-agent](../adapter/cli-agent.md)).
- [ ] `pnpm gate tests-reviewed <feature>` → PASS (gate [testes-revisados](../gate/testes-revisados.md)).
      Teste alterado depois da revisão volta para a etapa 3.
- [ ] `pnpm gate coverage <feature>` → PASS (gate [cobertura](../gate/cobertura.md)).

### 5. mutação e e2e
- [ ] `pnpm gate mutation <feature>` → PASS (gate [mutacao](../gate/mutacao.md)): score dos `domainSources` maior ou
      igual a `gates.mutation.min_score` do `harness.project.yaml`.
- [ ] `pnpm gate e2e <feature>` → PASS (gate [e2e](../gate/e2e.md)).
- [ ] FAIL: voltar à etapa 2 (faltou teste; mutante sobrevivente pede caso novo) ou 4 (código errado). Teste novo
      passa de novo pelas etapas 3 e 4.

### 6. revisão final
- [ ] Chamar **reviewer** para o diff; acrescentar a saída a `revisao.md` (gate [auditoria](../gate/auditoria.md),
      manual).
- [ ] Reprovado: voltar à etapa 4 (ou 2, se faltar teste).

### 7. fechamento
- [ ] Achado da revisão final que não foi corrigido na hora (inclusive "baixa" e "risco aceito") vira defeito:
      `docs/defeitos/D<n>.md`, com origem `revisão` e esta feature como feature de origem
      ([workflow defeito](defeito.md)). O `revisao.md` cita o `D<n>`.
- [ ] `pnpm verify` verde (gate [done-checks](../gate/done-checks.md)).
- [ ] `stages.review = done` no estado; commit com hooks, citando o ID da feature.

Regras que valem em todas as etapas: [policy/quality.md](../policy/quality.md).

## Pronto quando
Todos os gates abaixo estão **PASS**; qualquer FAIL ou SKIP bloqueia o aceite.

- [ ] [expect-red](../gate/expect-red.md): PASS antes da implementação.
- [ ] [testes-revisados](../gate/testes-revisados.md): sha atual de cada teste bate com o sha assinado.
- [ ] [testes-revisados](../gate/testes-revisados.md): todo critério tem teste da camada declarada.
- [ ] [cobertura](../gate/cobertura.md): 100% nos arquivos de `sources` da feature (limites do gate, não do
      `vitest.config`), sem teste pulado e sem 0/0.
- [ ] [mutacao](../gate/mutacao.md): score dos arquivos de domínio maior ou igual ao mínimo do projeto.
- [ ] [e2e](../gate/e2e.md): fluxos e2e da feature passam; lista vazia só sem critério e2e.
- [ ] [auditoria](../gate/auditoria.md): veredito `aprovado` em `revisao.md`; achado não corrigido registrado como
      defeito.
- [ ] [done-checks](../gate/done-checks.md): `pnpm verify` verde.

## Falhas
Status de etapa e o que fazer em cada falha: [feature-artefatos/estado.md](feature-artefatos/estado.md#status-das-etapas).
