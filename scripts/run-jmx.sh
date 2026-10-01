#!/usr/bin/env bash
# 生成したシナリオを JMeter 5.6.3 の非GUIモードで1回実行します。
# 使い方: ./scripts/run-jmx.sh path/to/plan.jmx
# 同じ名前の環境変数があれば、一時プロパティファイル経由で渡します。
# JMeter は -J の値をログに書くため、値そのものはコマンドラインに載せません。
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  printf '%s\n' "使い方: run-jmx.sh シナリオ.jmx [JMeterへ渡す追加引数]" >&2
  exit 2
fi

plan_input=$1
shift

if [ ! -f "$plan_input" ]; then
  printf '%s\n' "シナリオファイルがありません: $plan_input" >&2
  exit 3
fi

if ! command -v java >/dev/null 2>&1; then
  printf '%s\n' "Java が見つかりません。JMeter 5.6.3 は Java 8 以上で動き、Java 17 を推奨します。" >&2
  exit 127
fi

if [ -n "${JMETER_HOME:-}" ] && [ -x "${JMETER_HOME}/bin/jmeter" ]; then
  jmeter_bin="${JMETER_HOME}/bin/jmeter"
elif command -v jmeter >/dev/null 2>&1; then
  jmeter_bin=$(command -v jmeter)
else
  printf '%s\n' "JMeter が見つかりません。JMETER_HOME を設定するか、jmeter を PATH に入れてください。" >&2
  exit 127
fi

plan_dir=$(CDPATH= cd -- "$(dirname -- "$plan_input")" && pwd)
plan_name=$(basename -- "$plan_input")
plan_path="${plan_dir}/${plan_name}"
cd "$plan_dir"
mkdir -p results
rm -f results/jmeter-results.jtl

properties=(
  API_PROTOCOL
  API_HOST
  API_PORT
  X_API_KEY
  CONNECT_TIMEOUT_MS
  RESPONSE_TIMEOUT_MS
  COGNITO_TOKEN_MODE
  COGNITO_REGION
  COGNITO_CLIENT_ID
  COGNITO_CLIENT_SECRET
  COGNITO_SCOPES
  COGNITO_DOMAIN
  COGNITO_GET_CLIENT_TOKEN_PROTOCOL
  COGNITO_GET_CLIENT_TOKEN_HOST
  COGNITO_GET_CLIENT_TOKEN_PORT
  COGNITO_OAUTH_PROTOCOL
  COGNITO_OAUTH_PORT
  TOKEN_REFRESH_SKEW_SECONDS
)

escape_property() {
  local input=$1
  local flat
  flat=$(printf '%s' "$input" | od -An -tu1 | tr -s ' \n' ' ')
  local -a parts
  read -r -a parts <<< "$flat"
  local index=0
  local total=${#parts[@]}
  local output=""
  while [ "$index" -lt "$total" ]; do
    local byte=${parts[$index]}
    local code=$byte
    local width=1
    if [ "$byte" -ge 192 ] && [ "$byte" -lt 224 ]; then
      code=$(( ((byte & 31) << 6) | (${parts[$((index + 1))]} & 63) ))
      width=2
    elif [ "$byte" -ge 224 ] && [ "$byte" -lt 240 ]; then
      code=$(( ((byte & 15) << 12) | ((${parts[$((index + 1))]} & 63) << 6) | (${parts[$((index + 2))]} & 63) ))
      width=3
    elif [ "$byte" -ge 240 ]; then
      code=$(( ((byte & 7) << 18) | ((${parts[$((index + 1))]} & 63) << 12) | ((${parts[$((index + 2))]} & 63) << 6) | (${parts[$((index + 3))]} & 63) ))
      width=4
    fi
    if [ "$code" -eq 92 ]; then
      output="${output}\\\\"
    elif [ "$code" -eq 10 ]; then
      output="${output}\\n"
    elif [ "$code" -eq 13 ]; then
      output="${output}\\r"
    elif [ "$code" -eq 9 ]; then
      output="${output}\\t"
    elif [ "$code" -lt 32 ] || [ "$code" -gt 255 ]; then
      output="${output}$(printf '\\u%04x' "$code")"
    else
      output="${output}$(printf "\\$(printf '%03o' "$code")")"
    fi
    index=$((index + width))
  done
  printf '%s' "$output"
}

override_file="${plan_dir}/results/jmeter-overrides.properties"
cleanup_override() { rm -f "$override_file"; }
trap cleanup_override EXIT
: >"$override_file"
override_count=0
for name in "${properties[@]}"; do
  if [ "${!name+x}" = "x" ]; then
    printf '%s=%s\n' "$name" "$(escape_property "${!name}")" >>"$override_file"
    override_count=$((override_count + 1))
    printf '%s\n' "プロパティを上書きします: ${name}"
  fi
done

printf '%s\n' "シナリオ: ${plan_path}"
printf '%s\n' "結果: ${plan_dir}/results/jmeter-results.jtl"
printf '%s\n' "ログ: ${plan_dir}/results/jmeter.log"
set +e
if [ "$override_count" -gt 0 ]; then
  "$jmeter_bin" -n -t "$plan_path" -j "${plan_dir}/results/jmeter.log" -q "$override_file" "$@"
else
  "$jmeter_bin" -n -t "$plan_path" -j "${plan_dir}/results/jmeter.log" "$@"
fi
status=$?
set -e
exit "$status"
