# Gate: artifact-lint

## Objetivo

Rejeitar planos e cartões incompletos antes de iniciar uma etapa que consome modelo ou altera arquivos.

## Comando

`python3 <harness>/script/artifact_lint.py <raiz do projeto>`

Com `--strict`, valida cartões que declaram `schemaVersion: 2`, planos JSON V2 e dependências sem alterar os
artefatos legados. Cartões V2 também precisam ter paths existentes ou declarados em `generatedPaths`.

## Compatibilidade

- Modo padrão valida estrutura e preserva cartões V1.
- Modo estrito valida apenas artefatos explicitamente migrados para o schema 2.
- Cartões V1 não são reescritos automaticamente nem bloqueados por campos novos.
- Dependência pode apontar para outro cartão ou para uma feature com `plano.md`.
- `estado.json` sem `schemaVersion` é tratado como legado; com versão 2, `testShas` é verificado contra os bytes dos testes.

## Critério de sucesso

- YAML de cartões válido;
- IDs sem duplicação;
- paths relativos e seguros nos contratos estritos;
- cartões executáveis com scope;
- referências de dependência resolvidas;
- planos com estrutura de critérios adequada ao modo selecionado.

## Critério de falha

Qualquer erro estruturado. O gate informa o arquivo e o campo para correção.
