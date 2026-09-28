#!/usr/bin/env bash
# Push with a runtime-only credential. No token is ever written to .git/config
# or embedded in the remote URL.
#
#   scripts/git-push.sh a origin main
#   scripts/git-push.sh b origin b/api-routes
set -euo pipefail

who="${1:?usage: git-push.sh a|b <remote> <refspec> [extra git push args...]}"
remote="${2:?usage: git-push.sh a|b <remote> <refspec> [extra git push args...]}"
shift 2

case "$who" in
  a) TOKEN="${GH_TOKEN_A:?GH_TOKEN_A is not set}" ;;
  b) TOKEN="${GH_TOKEN_B:?GH_TOKEN_B is not set}" ;;
  *) echo "identity must be a or b" >&2; exit 1 ;;
esac

# GitHub accepts x-access-token:<PAT> as HTTP basic auth for git.
CREDENTIAL="$(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')"

exec git -c "http.extraHeader=AUTHORIZATION: Basic ${CREDENTIAL}" push "$remote" "$@"
