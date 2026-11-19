# Gate: card-skills

## Objetivo
Garantir que a tarefa que mexe em código de frontend (ou em outro caminho que o projeto declare) leia as regras
certas: o cartão traz `skills:` e cada item existe.

## Critério de sucesso (PASS)
- todo cartão (lista `cards` dos arquivos de `cards_glob` do `harness.project.yaml`) cujo `allowedPaths` tem algum
  caminho igual a um prefixo de `skills_required_paths`, ou dentro dele, declara `skills:` não vazio;
- todo item de `skills:` de qualquer cartão é `nome` ou `nome#regra` e existe: `<skill_dir>/<nome>/SKILL.md` e, com
  regra, `<skill_dir>/<nome>/rules/<regra>.md` ([formato](../workflow/feature-artefatos/cartao.md)).

## Critério de falha (FAIL)
Cartão de caminho exigido sem `skills:`; skill ou regra inexistente; item mal formado; configuração ausente
(`cards_glob`, `skills_required_paths`) ou arquivo de cartões ilegível.

## Evidência esperada
Saída de `python3 script/check_card_skills.py <raiz do projeto>`: "Gate card-skills passou." (saída 0) ou uma linha
por cartão com problema (saída 2).
