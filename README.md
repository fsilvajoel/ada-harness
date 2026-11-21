# Harness Ada

## Versão atual

- V1 compatível: workflow, papéis, adaptadores, gates e estado existentes;
- evolução 2.0 incremental: primitives de pipeline, schemas, política condicional, dashboard com diffs e aprovação SDD;
- plano operacional: [EvolucaoAdaHarness2.0.md](EvolucaoAdaHarness2.0.md).

O runner 2.0 despacha e executa uma stage configurada em worktree isolada. Ele ainda não coordena uma feature inteira
a partir de PRD/ADR/RFC: geração automática de plano/cartões e driver sequencial permanecem necessários antes de
declarar autonomia ponta a ponta.

Harness de processo para agentes de IA, agnóstico ao modelo e à ferramenta. Trabalha **sobre** um repositório
alvo e não pertence a ele: tudo o que é do projeto (papéis, comandos, skills, textos proibidos) fica no
`harness.project.yaml` do próprio projeto (molde: [harness.project.example.yaml](harness.project.example.yaml)).

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `agent/` | papéis (planner, refiner, test-writer, reviewer, implementer) |
| `adapter/` | como cada ferramenta de IA executa os papéis e as travas |
| `skill/` | skills compartilhadas por todos os papéis (próprias e de terceiros) |
| `capability/` | comportamentos que juntam skills, policies e gates |
| `policy/` | regras globais (inclui [idioma](policy/idioma.md)) |
| `workflow/` | sequência de etapas e passagens |
| `gate/` | critérios que aprovam ou bloqueiam (PASS/FAIL) |
| `script/` | utilitários V1 e componentes 2.0 (pipeline, artefatos, SDD, dashboard e métricas) |
| `tests/` | validação do próprio harness |

## Regras de arquitetura
- Agente é papel, não tecnologia; quem executa cada papel vem de `roles` no yaml.
- Skill é conhecimento e boa prática.
- Capability agrupa skills, policies e gates num comportamento reutilizável.
- Policy define regra do sistema todo; regra repetida vira policy.
- Script executa ação; texto não executa nada.
- Gate responde PASS/FAIL contra critério objetivo.
- Workflow orquestra a sequência de trabalho.

## Contrato
- Documentação em Markdown, em pt-BR ([policy/idioma.md](policy/idioma.md)).
- Lógica de execução em `script/` (harness) e, por enquanto, em `scripts/` do projeto (gates, hook).
- Ponto de entrada: o workflow [feature](workflow/feature.md), conduzido por qualquer agente de IA (orquestrador).
- Skills, gates, policies e moldes são **compartilhados**: ficam em [skill/](skill/README.md), `gate/` etc., e todo papel lê todos ([decisão](capability/recursos-compartilhados.md)). O `skill_dir` do `harness.project.yaml` aponta para `skill/`; o adaptador pode ligar um caminho local por link simbólico.
- Configuração real do projeto no `harness.project.yaml` dele (papéis, comandos, checks, textos proibidos).
