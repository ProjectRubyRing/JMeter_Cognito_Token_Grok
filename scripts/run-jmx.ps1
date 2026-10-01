# 生成したシナリオを JMeter 5.6.3 の非GUIモードで実行します。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-jmx.ps1 .\examples\petstore.jmx
# 同じ名前の環境変数があれば、一時プロパティファイル経由で渡します。
# JMeter は -J の値をログに書くため、値そのものはコマンドラインに載せません。
$ErrorActionPreference = "Stop"

function Write-Line([string]$Message) {
    [Console]::Out.WriteLine($Message)
}

if ($args.Count -lt 1 -or $args[0] -eq "-h" -or $args[0] -eq "--help") {
    Write-Line "使い方: run-jmx.ps1 シナリオ.jmx [JMeterへ渡す追加引数]"
    exit 2
}

$planInput = [string]$args[0]
$extra = @()
if ($args.Count -gt 1) {
    $extra = @($args[1..($args.Count - 1)])
}

if (-not (Test-Path -LiteralPath $planInput -PathType Leaf)) {
    Write-Line "シナリオファイルがありません: $planInput"
    exit 3
}

if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Write-Line "Java が見つかりません。JMeter 5.6.3 は Java 8 以上で動き、Java 17 を推奨します。"
    exit 127
}

$jmeter = $null
if ($env:JMETER_HOME) {
    $candidate = Join-Path $env:JMETER_HOME "bin\jmeter.bat"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $jmeter = $candidate
    }
}
if (-not $jmeter) {
    $command = Get-Command jmeter.bat -ErrorAction SilentlyContinue
    if ($command) { $jmeter = $command.Source }
}
if (-not $jmeter) {
    $command = Get-Command jmeter -ErrorAction SilentlyContinue
    if ($command) { $jmeter = $command.Source }
}
if (-not $jmeter) {
    Write-Line "JMeter が見つかりません。JMETER_HOME を設定するか、jmeter.bat を PATH に入れてください。"
    exit 127
}

$full = (Resolve-Path -LiteralPath $planInput).Path
$dir = Split-Path -Parent $full
Set-Location -LiteralPath $dir
$results = Join-Path $dir "results"
if (-not (Test-Path -LiteralPath $results)) {
    New-Item -ItemType Directory -Path $results | Out-Null
}
$jtl = Join-Path $results "jmeter-results.jtl"
if (Test-Path -LiteralPath $jtl) {
    Remove-Item -LiteralPath $jtl -Force
}
$log = Join-Path $results "jmeter.log"

$names = @(
    "API_PROTOCOL", "API_HOST", "API_PORT", "X_API_KEY",
    "CONNECT_TIMEOUT_MS", "RESPONSE_TIMEOUT_MS",
    "COGNITO_TOKEN_MODE", "COGNITO_REGION", "COGNITO_CLIENT_ID", "COGNITO_CLIENT_SECRET",
    "COGNITO_SCOPES", "COGNITO_DOMAIN",
    "COGNITO_GET_CLIENT_TOKEN_PROTOCOL", "COGNITO_GET_CLIENT_TOKEN_HOST", "COGNITO_GET_CLIENT_TOKEN_PORT",
    "COGNITO_OAUTH_PROTOCOL", "COGNITO_OAUTH_PORT",
    "TOKEN_REFRESH_SKEW_SECONDS"
)
function ConvertTo-JavaPropertyValue([string]$Value) {
    if ($null -eq $Value) { return "" }
    $builder = New-Object System.Text.StringBuilder
    foreach ($char in $Value.ToCharArray()) {
        $code = [int]$char
        if ($char -eq "\") { $null = $builder.Append("\\") }
        elseif ($char -eq "`n") { $null = $builder.Append("\n") }
        elseif ($char -eq "`r") { $null = $builder.Append("\r") }
        elseif ($char -eq "`t") { $null = $builder.Append("\t") }
        elseif ($code -lt 32 -or $code -gt 255) { $null = $builder.Append("\u" + $code.ToString("x4")) }
        else { $null = $builder.Append($char) }
    }
    return $builder.ToString()
}

$lines = New-Object System.Collections.Generic.List[string]
foreach ($name in $names) {
    if (Test-Path -LiteralPath "Env:$name") {
        $value = (Get-Item -LiteralPath "Env:$name").Value
        $null = $lines.Add($name + "=" + (ConvertTo-JavaPropertyValue $value))
        Write-Line "プロパティを上書きします: $name"
    }
}

Write-Line "シナリオ: $full"
Write-Line "結果: $jtl"
Write-Line "ログ: $log"
$overrideFile = $null
$code = 0
try {
    $jmeterArgs = @("-n", "-t", $full, "-j", $log)
    if ($lines.Count -gt 0) {
        $overrideFile = Join-Path $results "jmeter-overrides.properties"
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($overrideFile, (($lines -join "`n") + "`n"), $utf8)
        $jmeterArgs += @("-q", $overrideFile)
    }
    foreach ($item in $extra) { $jmeterArgs += [string]$item }
    & $jmeter @jmeterArgs
    if ($null -ne $LASTEXITCODE) { $code = [int]$LASTEXITCODE }
} finally {
    if ($overrideFile -and (Test-Path -LiteralPath $overrideFile)) {
        Remove-Item -LiteralPath $overrideFile -Force
    }
}
exit $code
