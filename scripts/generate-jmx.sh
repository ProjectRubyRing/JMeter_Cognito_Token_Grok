#!/usr/bin/env bash
# OpenAPI 定義から JMeter 5.6.3 のシナリオを作ります。RHEL 9.8 で実行します。
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' "python3 がありません。RHEL 9.8 では次で入ります: sudo dnf install -y python3" >&2
  exit 127
fi

exec python3 "$SCRIPT_DIR/openapi_to_jmx.py" "$@"
