# Gate: edit-guard

## Objetivo
Impedir, antes de a tool rodar, ações que pulam os hooks do git, tocam segredos ou escrevem fora do repositório.

## Executor
Trava do adaptador de cada ferramenta, rodada antes de toda ação de shell, leitura ou escrita. Bloqueio com motivo;
evento inválido também bloqueia (falha fechada). Hoje existe no [adaptador do Claude Code](../adapter/claude-code.md).

## Critério de sucesso (PASS)
A chamada não cai em nenhuma regra abaixo e segue normalmente.

## Critério de falha (FAIL)
- comando de shell com `--no-verify` (inclusive abreviado), `git commit -n` (também agrupado, como `-an`), troca de
  `core.hooksPath` ou `LEFTHOOK=`;
- qualquer tool ou comando que cite `.env` ou variante (`.env.local`, `.env*`); `.env.example` é permitido;
- escrita com caminho fora do repositório (symlinks e `..` resolvidos), exceto as áreas de memória e temp que o
  adaptador liberar; `.env` continua bloqueado também nessas áreas.

## Limites
Não analisa para onde um comando de shell escreve (redirecionamento, `cp`, `mv`); escopo do plano continua sendo
conferido na revisão. Regra de fundo: [policy/editing.md](../policy/editing.md).

## Evidência esperada
Motivo do bloqueio devolvido ao agente; testes da trava no adaptador.
