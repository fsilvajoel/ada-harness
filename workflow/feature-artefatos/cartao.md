# Cartão: campo `skills`

Os cartões das tarefas ficam no arquivo do plano do projeto (ex.: `docs/plano/E<n>.yaml`, lista `cards`). O harness
só define o campo abaixo; os demais campos são do projeto.

## `skills` (opcional, por tarefa)

Skills e regras **extras** que quem executa a tarefa precisa ler, além das obrigatórias do papel
([role_skills](../../capability/recursos-compartilhados.md#skills-obrigatórias-por-papel)). O cartão (que vem do
plano, decidido no SDD) declara; o orquestrador só aponta o cartão e repassa o campo, ou o papel o lê sozinho.

```yaml
- id: E7-T09
  kind: implement
  skills:
    - react-best-practices                              # o SKILL.md (índice) da skill
    - react-best-practices#rerender-memo-with-default-value   # o índice + rules/<regra>.md
```

- Cada item é `nome` ou `nome#regra`. `nome` = pasta em `skill_dir`; `regra` = nome do arquivo em
  `skill/<nome>/rules/` sem o `.md`.
- Resolução: `nome` → `skill/<nome>/SKILL.md`; `nome#regra` → o mesmo `SKILL.md` e `skill/<nome>/rules/<regra>.md`.
  Só esses dois tipos de arquivo entram: o `AGENTS.md` compilado de uma skill **nunca** é injetado (peso).
- Sem `skills`, vale só a lista do papel. Item repetido, ou que já está na lista do papel, entra uma vez.
- Skill ou regra inexistente é erro claro, não é ignorada.
- **Obrigatório** em cartão cujo `allowedPaths` toca um caminho de `skills_required_paths` do projeto (ex.: o app
  web): o refiner preenche (ex.: `react-best-practices#<regra>` pertinente) e o gate
  [card-skills](../../gate/card-skills.md) falha sem isso ou com item inexistente.

## Quem lê
- Trabalhador **sem ferramentas** (api-chat, cli-agent): o executor inclui os recursos no pedido
  (`script/api_chat.py` e `script/cli_agent.py`, ambos com `--role <papel> --skills <itens do cartão>`;
  `script/skill_inject.py` imprime o mesmo bloco). Detalhes e teto de tamanho em [api-chat](../../adapter/api-chat.md#skills-e-regras-no-pedido).
- Agente **com ferramentas** (Claude Code): o molde do papel manda ler as do papel e as do campo `skills` do cartão
  antes de começar.
