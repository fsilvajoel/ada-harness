# Gate: sdd-approval

## Objetivo

Impedir que uma especificação alterada ou ainda não aprovada gere uma feature executável.

## Comando

`python3 script/sdd.py validate <diretório SDD>`

Para registrar uma aprovação explícita, o responsável executa:

`python3 script/sdd.py approve <prd|adr|rfc|plan> <documento> <documento.approval.json> --reviewer <identidade>`

A aprovação contém o SHA-256 do documento. Qualquer edição posterior invalida a aprovação. Documentos com questões abertas
não avançam.

Com as aprovações do PRD/ADR/RFC e do plano técnico válidas, publique os artefatos validados:

`python3 script/sdd.py prepare-feature <raiz do projeto> --sdd-directory <diretório SDD> --plan <plano.json> --cards <cards.yaml> --approval <plano.approval.json>`

## Critério de sucesso

- PRD, ADR e RFC contêm as seções obrigatórias;
- cada documento tem aprovação schemaVersion 2;
- o hash corresponde aos bytes atuais;
- reviewer está identificado e não há questões abertas.

## Critério de falha

Documento ausente, seção obrigatória ausente, hash divergente ou aprovação incompleta.
