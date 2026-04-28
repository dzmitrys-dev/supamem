#!/usr/bin/env bash
# supamem PreToolUse(Edit|Write) guard — injects top-k semantic chunks for the
# file being edited. Project root via $SUPAMEM_PROJECT_DIR (fallback $PWD).
#
# Wired in by `supamem install --client claude-code`. To disable per-session:
#   export SUPAMEM_HOOK_DISABLED=1
#
# Reads the hook payload from stdin (Claude Code passes JSON with file_path),
# delegates to `supamem hook claude-code`, and emits hookSpecificOutput to
# stdout. Fail-soft: any exception from supamem returns empty output and
# exit 0 (the underlying Edit/Write must never be blocked).

set -u

if [[ "${SUPAMEM_HOOK_DISABLED:-0}" == "1" ]]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":""}}'
  exit 0
fi

PROJECT_DIR="${SUPAMEM_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" 2>/dev/null || true

# Read JSON payload from stdin and extract file_path. Use python because we
# already depend on it (supamem is a Python package).
FILE_PATH="$(python3 -c 'import json, sys; d = json.load(sys.stdin); print(d.get("tool_input", {}).get("file_path", ""))' 2>/dev/null || echo "")"

if [[ -z "$FILE_PATH" ]]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":""}}'
  exit 0
fi

# Delegate. supamem hook is fail-soft itself, but we double-guard with || true.
supamem hook claude-code --file-path "$FILE_PATH" 2>/dev/null || \
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":""}}'

exit 0
