# Harness-adaV1

Harness de processo para agentes de IA, agnóstico ao modelo e à ferramenta. Trabalha **sobre** um repositório
alvo e não pertence a ele: tudo o que é do projeto (papéis, comandos, skills, textos proibidos) fica no
`harness.project.yaml` do próprio projeto (molde: [harness.project.example.yaml](harness.project.example.yaml)).

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `agent/` | papéis (planner, test-writer, reviewer, implementer) |
| `adapter/` | como cada ferramenta de IA executa os papéis e as travas |
| `capability/` | comportamentos que juntam skills, policies e gates |
| `policy/` | regras globais (inclui [idioma](policy/idioma.md)) |
| `workflow/` | sequência de etapas e passagens |
| `gate/` | critérios que aprovam ou bloqueiam (PASS/FAIL) |
| `script/` | utilitários do harness (checks portable e skill-format, executor api-chat) |
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
- Skills ficam no projeto, na pasta `skill_dir` do `harness.project.yaml`; o harness cita só o nome delas.
- Configuração real do projeto no `harness.project.yaml` dele (papéis, comandos, checks, textos proibidos).
