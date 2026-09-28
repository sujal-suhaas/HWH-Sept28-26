#!/usr/bin/env bash
# The only sanctioned way to commit in this repository.
# Author and committer identity are always explicit; never rely on global config.
set -euo pipefail

who="${1:?usage: git-as.sh a|b ...}"
shift

case "$who" in
  a)
    NAME="${MEMBER_A_NAME:?MEMBER_A_NAME is not set}"
    EMAIL="${MEMBER_A_EMAIL:?MEMBER_A_EMAIL is not set}"
    ;;
  b)
    NAME="${MEMBER_B_NAME:?MEMBER_B_NAME is not set}"
    EMAIL="${MEMBER_B_EMAIL:?MEMBER_B_EMAIL is not set}"
    ;;
  *)
    echo "identity must be a or b" >&2
    exit 1
    ;;
esac

GIT_AUTHOR_NAME="$NAME" \
GIT_AUTHOR_EMAIL="$EMAIL" \
GIT_COMMITTER_NAME="$NAME" \
GIT_COMMITTER_EMAIL="$EMAIL" \
git commit "$@"
