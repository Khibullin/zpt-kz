#!/usr/bin/env bash
set -o errexit

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" manage.py migrate --noinput
"$PYTHON" manage.py collectstatic --noinput
