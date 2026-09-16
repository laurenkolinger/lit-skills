#!/usr/bin/env bash
# Install the literature library skills and set up a library folder.
set -euo pipefail

LIBRARY="${1:-}"
if [ -z "$LIBRARY" ]; then
  echo "usage: ./install.sh /path/to/your/library" >&2
  exit 2
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILLS="$HOME/.claude/skills"

echo "Installing skills into $SKILLS"
mkdir -p "$SKILLS/lit-ingest" "$SKILLS/lit-search"
cp "$HERE/skills/lit-ingest/SKILL.md" "$SKILLS/lit-ingest/SKILL.md"
cp "$HERE/skills/lit-search/SKILL.md" "$SKILLS/lit-search/SKILL.md"

echo "Setting up the library at $LIBRARY"
mkdir -p "$LIBRARY/pdfs" "$LIBRARY/ingest/_processed" "$LIBRARY/logs" "$LIBRARY/skill"
cp -R "$HERE/litkit" "$LIBRARY/skill/"
cp -R "$HERE/tests" "$LIBRARY/skill/"

echo
echo "Done. Add this to your shell profile:"
echo
echo "    export VICAR_LIT_HOME=\"$LIBRARY\""
echo "    export VICAR_LIT_CONTACT_EMAIL=\"you@example.org\"   # for OpenAlex"
echo
echo "Then drop PDFs into $LIBRARY/ingest and ask your agent to run the lit-ingest skill."
