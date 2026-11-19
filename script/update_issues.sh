#!/usr/bin/env bash
# Atualiza as issues em pt-BR: renderiza -> traduz (Ollama) -> confere -> dry-run do sync.
# Uso: update_issues.sh <raiz do projeto> <repo owner/nome>   (env: OLLAMA_URL, OLLAMA_MODEL; não aplica nada no GitHub)
set -euo pipefail
root=${1:?raiz do projeto}; repo=${2:?owner/repo}
here=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-$here/.venv/bin/python}
work=$(mktemp -d)
"$py" "$here/script/render_issue.py" "$root" --all --out "$work" --repo-url "https://github.com/$repo"
plano="$root/docs/plano"
fail=0
for f in "$work"/*.md; do
  id=$(basename "$f" .md)
  [ -d "$plano/$id" ] || continue   # defeitos já nascem em pt-BR
  "$py" "$here/script/translate_blocks.py" "$f" --out "$plano/$id/issue.pt-br.md" --cache "$plano/$id/issue.cache.json" || fail=1
done
"$py" "$here/script/check_issue_render.py" "$work" --translated >/dev/null 2>&1 || true
echo "traduções em $plano/<feature>/issue.pt-br.md (revisar antes do --apply)"
"$py" "$here/script/sync_github_project.py" "$root" --repo "$repo" | head -5
exit $fail
