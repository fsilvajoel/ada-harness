# Scripts

Lógica de orquestração e utilitários de execução do projeto.

Scripts coordenam o harness, mas não guardam a policy canônica nem o conhecimento de domínio, que fica nas skills.
Despacho: `dispatch.py` (escolhe api_chat.py ou cli_agent.py pelo `roles.<papel>.worker`).
Executores e gates: `api_chat.py` (api-chat), `cli_agent.py` (cli-agent), `skill_inject.py` (bloco de skills),
`check_portable.py`, `check_skill_format.py`, `check_card_skills.py`, `check_role_skills.py`,
`check_status_consistency.py`, `render_issue.py`, `check_issue_tokens.py`, `translate_blocks.py`,
`check_issue_render.py` (corpo das issues).

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
  uma por defeito (`<!-- ada-defeito:<ID> -->`, rótulo `bug`; status fechado fecha a issue). O corpo é o de
  `render_issue.py` (com `--repo-url` derivado de `--repo`) ou, se existir, o
  `<pasta dos cartões>/<feature>/issue.pt-br.md`. Rótulos do renderizador que faltam no repositório são criados só com
  `--apply`. Doc ou link quebrado = saída 1. Campo `Status` do Project v2 na etapa. Sem `--apply` só imprime o plano. `--apply` exige o escopo `project` no `gh`;
  sem ele sai com 3 e mostra `gh auth refresh -s project` (interativo, só o humano roda). Texto das issues passa por
  filtro (IPs, caminhos de máquina, chaves, `.env`); achado = saída 4 e nada é enviado. Opções: `--repo`, `--owner`,
  `--project-number N` ou `--create-project --project-title T`, `--branch` (padrão `main`).
- `render_issue.py <raiz> [--feature ID | --defeito Dn | --all] [--out pasta] [--repo-url URL] [--branch NOME]`:
  renderizador determinístico (sem IA) do corpo das issues. Gera um `.md` por feature e por defeito com seções fixas em
  pt-BR (resumo e escopo, regras e critérios, contrato, cartões com DoR e DoD, referências, telas, estado), lendo
  `plano.md`, o PRD (regras citadas em `refs`), ADRs/RFCs, `docs/defeitos/D<n>.md` e o estado do `status.py`. O inglês
  dos cartões (título, `done`, `hints`) sai embrulhado em `<!--t:HASH-->texto<!--/t-->` (HASH = sha256 curto), para a
  tradução; o que já é pt-BR é copiado sem marca. Com `--repo-url` os links viram absolutos e as imagens
  `<url>/blob/<branch>/<caminho>?raw=true`. Link relativo de doc ou imagem inexistente = saída 1 e nada é gravado;
  corpo acima de 65000 caracteres é truncado com aviso e link do doc. `--all` exige `--out`. Chave opcional do yaml:
  `docs_dir` (padrão `docs`). Testes: `tests/test_render_issue.py` (os que leem um projeto real usam a variável
  `ADA_TEST_PROJECT`).
- `check_issue_tokens.py ORIGINAL.md TRADUZIDO.md`: compara código, IDs, caminhos e números entre original e
  tradução (multiconjunto). Saída 0 ok, 1 diferenças (linhas `faltando:`/`sobrando:`), 2 uso.
- `translate_blocks.py <issue.md> [--ollama-url URL] [--model NOME] [--cache arq.json] [--out arq.md]`: traduz os
  blocos `<!--t:HASH-->` com um modelo via HTTP Ollama (`OLLAMA_URL` e `OLLAMA_MODEL` como padrão; prompt fixo; cada
  tradução passa pelo `check_issue_tokens`, com até 2 novas tentativas, e a falha não grava nada). Cache
  `issue.cache.json` (hash -> tradução) e saída `issue.pt-br.md` ao lado do `issue.md`. Sem URL só reporta quantos
  blocos faltam. Saída 0 ok, 1 reprovada ou modelo inacessível, 2 uso.
- `check_issue_render.py <pasta> [--root RAIZ] [--translated]`: gate dos `.md` renderizados/traduzidos: corpo acima de
  65000 caracteres, link ou imagem relativa que não resolve (em relação ao arquivo ou a `--root`), inglês residual na
  prosa (palavras vazias do inglês fora de código, URLs e blocos `<!--t:-->`; o título `# ...` da linha 1 fica de
  fora) e, com `--translated`, bloco sem tradução. Saída 0 limpo, 1 uma linha por achado, 2 uso.
Molde: [_modelo.md](_modelo.md).
