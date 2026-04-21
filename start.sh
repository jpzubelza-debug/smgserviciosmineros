#!/usr/bin/env bash
set -euo pipefail

if command -v python >/dev/null 2>&1; then
	PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON_BIN="python3"
else
	echo "No se encontro Python en PATH" >&2
	exit 127
fi

echo "Iniciando con ${PYTHON_BIN} ($(${PYTHON_BIN} --version 2>&1))"
exec "${PYTHON_BIN}" -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
