#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workspace_runtime="$project_root/.tools/ollama-runtime/ollama"

if [ -x "$workspace_runtime" ]; then
  ollama_binary="$workspace_runtime"
elif command -v ollama >/dev/null 2>&1; then
  ollama_binary=$(command -v ollama)
else
  echo "Ollama is not installed. See https://ollama.com/download" >&2
  exit 1
fi

models_directory=${OLLAMA_MODELS:-"$project_root/.tools/ollama-models"}
mkdir -p "$models_directory"
export OLLAMA_MODELS="$models_directory"
export OLLAMA_HOST=${OLLAMA_HOST:-127.0.0.1:11434}

exec "$ollama_binary" serve
