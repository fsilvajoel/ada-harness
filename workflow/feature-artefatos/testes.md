# Testes <ID da feature>

| Cenário | Critério | Camada | Arquivo | Teste |
|---|---|---|---|---|
| C1 | A1 | unit | <caminho> | <nome do teste> |
| C2 | A1 | prop | <caminho> | <nome do teste> |

## Checklist de bordas
Por entrada da tabela de domínio do plano ([checklist](../../capability/testing.md#checklist-de-bordas)): cenário
que cobre cada item, ou o motivo de não se aplicar.

| Entrada | Vazio | Só espaços | Espaços em volta | 1e1 | Separador | Negativo | Zero | Limite | Limite+1 | Muito grande | Caractere inválido |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `<campo>` | C<n> | C<n> | C<n> | C<n> | C<n> | C<n> | C<n> | C<n> | C<n> | C<n> | n/a: <motivo> |

## Arquivos de teste
- <caminho> (unit, prop)
- <caminho> (e2e; "nenhum" se não há critério e2e)

## Expect-red
Saída de `pnpm gate expect-red <feature>`: <PASS/FAIL e resumo>.
