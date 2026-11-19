# Scripts

Lógica de orquestração e utilitários de execução do projeto.

Scripts coordenam o harness, mas não guardam a policy canônica nem o conhecimento de domínio, que fica nas skills.
Despacho: `dispatch.py` (escolhe api_chat.py ou cli_agent.py pelo `roles.<papel>.worker`).
Executores e gates: `api_chat.py` (api-chat), `cli_agent.py` (cli-agent), `skill_inject.py` (bloco de skills),
`check_portable.py`, `check_skill_format.py`, `check_card_skills.py`, `check_role_skills.py`,
`check_status_consistency.py`.

## Status do pipeline
- `status.py <raiz> [--out status.json] [--print]`: lê os cartões (`cards_glob`), o `<pasta dos cartões>/<feature>/estado.json`
  de cada feature e a fila de PM (`pm_queue`) e grava `status.json` (chave opcional `status_file` no yaml; padrão
  `status.json` na raiz). Fonte única são cartões + estado.json: o arquivo é derivado, não se edita à mão.
  Conteúdo: `queue` (feature por etapa: plan, refinement, tests, test_review, implement, mutation_e2e, final_review,
  closing, done; cada etapa com `ready` e `blocked`), `features` (etapa atual, `percent`, `blocked` com `quota`,
  `needs_input`, `failed`, `card_error` ou `error`), `cards` (done, ready, blocked ou waiting, derivado do estado da
  feature e de `dependsOn`), `deliveries` (percentual por entrega = média das features; entrega = prefixo do ID
  da feature antes de `-F<n>`) e `pm_queue`. Estados antigos sem `refinement`, `testReview` ou `gates` contam a etapa
  como cumprida quando uma posterior já andou. Pendência de PM aberta bloqueia a feature como `needs_input`.
- `sync_github_project.py <raiz> [--apply]`: uma issue por feature (marcador `<!-- ada-feature:<ID> -->`, idempotente) e
  campo `Status` do Project v2 na etapa. Sem `--apply` só imprime o plano. `--apply` exige o escopo `project` no `gh`;
  sem ele sai com 3 e mostra `gh auth refresh -s project` (interativo, só o humano roda). Texto das issues passa por
  filtro (IPs, caminhos de máquina, chaves, `.env`); achado = saída 4 e nada é enviado. Opções: `--repo`, `--owner`,
  `--project-number N` ou `--create-project --project-title T`.
Molde: [_modelo.md](_modelo.md).
