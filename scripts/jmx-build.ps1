# JMeter 5.6.3 のシナリオ本体。generate-jmx.ps1 から読み込む。
if (-not ("PatNode" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
public class PatNode {
    public string Kind;
    public string Text;
    public int Lower;
    public int Upper;
    public List<PatNode> Children = new List<PatNode>();
    public PatNode Child;
}
"@
}

$script:ValidateScript = @'
def mode = vars.get("COGNITO_TOKEN_MODE")
if (mode == null) {
    mode = ""
}
def errors = new ArrayList()
if (!"get_client_token".equals(mode) && !"oauth2_token".equals(mode)) {
    errors.add("COGNITO_TOKEN_MODE は get_client_token または oauth2_token にしてください。")
}
def clientId = vars.get("COGNITO_CLIENT_ID")
def secret = vars.get("COGNITO_CLIENT_SECRET")
if (clientId == null || clientId.trim().isEmpty() || clientId.startsWith("your-perf-")) {
    errors.add("COGNITO_CLIENT_ID に、性能試験専用アプリクライアントのIDを設定してください。")
}
if (secret == null || secret.trim().isEmpty() || secret.startsWith("your-perf-")) {
    errors.add("COGNITO_CLIENT_SECRET に、性能試験専用アプリクライアントのシークレットを設定してください。")
}
if ("oauth2_token".equals(mode)) {
    def domain = vars.get("COGNITO_DOMAIN")
    if (domain == null || domain.trim().isEmpty() || domain.startsWith("your-domain")) {
        errors.add("COGNITO_DOMAIN に、ユーザープールのドメインホストだけを設定してください。https:// は不要です。")
    }
}
if ("get_client_token".equals(mode)) {
    def region = vars.get("COGNITO_REGION")
    if (region == null || region.trim().isEmpty()) {
        errors.add("COGNITO_REGION を設定してください。例: ap-northeast-1")
    }
}
if (errors.isEmpty()) {
    SampleResult.setSuccessful(true)
    SampleResult.setResponseCodeOK()
    SampleResult.setResponseMessage("Cognito settings are present")
    SampleResult.setIgnore()
} else {
    def message = errors.join(" ")
    SampleResult.setSuccessful(false)
    SampleResult.setResponseCode("400")
    SampleResult.setResponseMessage(message)
    SampleResult.setResponseData(message, "UTF-8")
    SampleResult.setStopTest(true)
    log.error(message)
}
'@

$script:PrepareGetClientTokenScript = @'
import groovy.json.JsonOutput

def overrideHost = vars.get("COGNITO_GET_CLIENT_TOKEN_HOST")
if (overrideHost != null && overrideHost.trim().length() > 0) {
    vars.put("COGNITO_RESOLVED_HOST", overrideHost.trim())
} else {
    def region = vars.get("COGNITO_REGION")
    if (region == null || region.trim().isEmpty()) {
        region = "ap-northeast-1"
    }
    vars.put("COGNITO_RESOLVED_HOST", "cognito-idp." + region.trim() + ".amazonaws.com")
}
def body = new LinkedHashMap()
body.put("ClientId", vars.get("COGNITO_CLIENT_ID") == null ? "" : vars.get("COGNITO_CLIENT_ID"))
body.put("Secret", vars.get("COGNITO_CLIENT_SECRET") == null ? "" : vars.get("COGNITO_CLIENT_SECRET"))
def scopesRaw = vars.get("COGNITO_SCOPES")
if (scopesRaw != null && scopesRaw.trim().length() > 0) {
    def scopes = new ArrayList()
    for (String part : scopesRaw.split("[\\s,]+")) {
        if (part != null && part.trim().length() > 0) {
            scopes.add(part.trim())
        }
    }
    if (!scopes.isEmpty()) {
        body.put("Scopes", scopes)
    }
}
vars.put("COGNITO_TOKEN_BODY", JsonOutput.toJson(body))
log.info("Prepared GetClientToken request. host=" + vars.get("COGNITO_RESOLVED_HOST"))
'@

$script:PrepareOauthScript = @'
def encode = { String value ->
    if (value == null) {
        return ""
    }
    return java.net.URLEncoder.encode(value, "UTF-8")
}
def parts = new ArrayList()
parts.add("grant_type=client_credentials")
parts.add("client_id=" + encode(vars.get("COGNITO_CLIENT_ID")))
parts.add("client_secret=" + encode(vars.get("COGNITO_CLIENT_SECRET")))
def scopes = vars.get("COGNITO_SCOPES")
if (scopes != null && scopes.trim().length() > 0) {
    parts.add("scope=" + encode(scopes.trim()))
}
vars.put("COGNITO_OAUTH_BODY", parts.join("&"))
log.info("Prepared OAuth token request. host=" + vars.get("COGNITO_DOMAIN"))
'@

$script:StoreTokenScript = @'
def token = vars.get("ACCESS_TOKEN")
def expiresRaw = vars.get("EXPIRES_IN")
def secret = vars.get("COGNITO_CLIENT_SECRET")
def recorded = null
try {
    recorded = prev.getQueryString()
} catch (MissingMethodException ignored) {
    recorded = prev.getSamplerData()
}
if (recorded != null && secret != null && secret.length() > 0) {
    def scrubbed = recorded.replace(secret, "***")
    def encoded = java.net.URLEncoder.encode(secret, "UTF-8")
    if (!encoded.equals(secret)) {
        scrubbed = scrubbed.replace(encoded, "***")
    }
    try {
        prev.setQueryString(scrubbed)
    } catch (MissingMethodException ignored) {
        prev.setSamplerData(scrubbed)
    }
}
if (token == null || token.trim().isEmpty() || "TOKEN_NOT_FOUND".equals(token)) {
    props.put("ACCESS_TOKEN", "TOKEN_NOT_FOUND")
    props.put("TOKEN_EXPIRES_AT_MS", "0")
    prev.setSuccessful(false)
    prev.setResponseMessage("Cognitoのアクセストークンを応答から取り出せませんでした。クライアントID、シークレット、スコープ、トークン方式を確認してください。")
    prev.setStopTest(true)
    log.error("Cognito access token was not extracted. mode=" + vars.get("COGNITO_TOKEN_MODE"))
    return
}
long ttl = 3600L
try {
    if (expiresRaw != null && expiresRaw.trim().length() > 0) {
        ttl = Long.parseLong(expiresRaw.trim())
    }
} catch (NumberFormatException ignored) {
    ttl = 3600L
}
long skew = 60L
try {
    def skewRaw = vars.get("TOKEN_REFRESH_SKEW_SECONDS")
    if (skewRaw != null && skewRaw.trim().length() > 0) {
        skew = Long.parseLong(skewRaw.trim())
    }
} catch (NumberFormatException ignored) {
    skew = 60L
}
if (skew < 0L) {
    skew = 0L
}
if (ttl > 1L && skew >= ttl) {
    skew = ttl - 1L
}
long expiresAt = System.currentTimeMillis() + Math.max(1L, ttl - skew) * 1000L
synchronized (props) {
    props.put("ACCESS_TOKEN", token)
    props.put("TOKEN_EXPIRES_AT_MS", Long.toString(expiresAt))
}
def clientId = vars.get("COGNITO_CLIENT_ID")
def suffix = ""
if (clientId != null && clientId.length() > 0) {
    suffix = clientId.length() <= 4 ? clientId : clientId.substring(clientId.length() - 4)
}
log.info("Stored Cognito access token for reuse. mode=" + vars.get("COGNITO_TOKEN_MODE") + " expires_in_seconds=" + ttl + " refresh_skew_seconds=" + skew + " token_length=" + token.length() + " client_id_suffix=" + suffix)
'@

$script:RefreshCheckScript = @'
def token = props.get("ACCESS_TOKEN")
def expiresRaw = props.get("TOKEN_EXPIRES_AT_MS")
boolean needs = true
if (token != null) {
    def tokenText = token.toString()
    if (tokenText.trim().length() > 0 && !"TOKEN_NOT_FOUND".equals(tokenText) && expiresRaw != null) {
        try {
            needs = System.currentTimeMillis() >= Long.parseLong(expiresRaw.toString())
        } catch (NumberFormatException ignored) {
            needs = true
        }
    }
}
vars.put("TOKEN_NEEDS_REFRESH", needs ? "true" : "false")
SampleResult.setIgnore()
'@

$script:TokenAssertionScript = @'
def token = props.get("ACCESS_TOKEN")
if (token == null || token.toString().trim().isEmpty() || "TOKEN_NOT_FOUND".equals(token.toString())) {
    AssertionResult.setFailure(true)
    AssertionResult.setFailureMessage("Cognitoのアクセストークンが無いため、API Gatewayへ認可情報を渡せません。")
}
'@

function Out-Box($Value) {
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [bool] -or (Test-Number $Value)) { return $Value }
    return ,$Value
}
function Test-True($Value) { return ($Value -is [bool] -and $Value) }
function Test-Truthy($Value) {
    if ($null -eq $Value) { return $false }
    if ($Value -is [bool]) { return [bool]$Value }
    if ($Value -is [string]) { return $Value.Length -gt 0 }
    if (Test-Number $Value) { return ([double]$Value) -ne 0 }
    if ($Value -is [YamlMap] -or $Value -is [YamlSeq]) { return $Value.Count -gt 0 }
    return $true
}
function Test-Same($Left, $Right) {
    if ($null -eq $Left -and $null -eq $Right) { return $true }
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    if ($Left -is [bool] -or $Right -is [bool]) { return ($Left -is [bool] -and $Right -is [bool] -and ($Left -eq $Right)) }
    if ((Test-Number $Left) -and (Test-Number $Right)) { return ([double]$Left) -eq ([double]$Right) }
    return [string]::Equals([string]$Left, [string]$Right, [StringComparison]::Ordinal)
}
function Get-Node($Node, [string]$Key) {
    if ($Node -isnot [YamlMap]) { return $null }
    $value = $Node.Get($Key)
    if ($null -eq $value) { return $null }
    return ,$value
}
function Test-HasNode($Node, [string]$Key) {
    if ($Node -is [YamlMap]) { return $Node.Has($Key) }
    return $false
}
function New-StringList {
    $list = New-Object System.Collections.Generic.List[string]
    return ,$list
}
function Add-Text($List, [string]$Name, [string]$Value) {
    $null = $List.Add((New-Object TextPair($Name, $Value)))
}

function ConvertTo-XmlText($Value) {
    if ($null -eq $Value) { return "" }
    return ([string]$Value).Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace('"', "&quot;")
}
function Format-StringProp([string]$Name, $Value) {
    return '<stringProp name="' + (ConvertTo-XmlText $Name) + '">' + (ConvertTo-XmlText $Value) + '</stringProp>'
}
function Format-BoolProp([string]$Name, [bool]$Value) {
    $text = "false"
    if ($Value) { $text = "true" }
    return '<boolProp name="' + (ConvertTo-XmlText $Name) + '">' + $text + '</boolProp>'
}
function Reset-Xml { $script:XmlParts = New-Object System.Collections.Generic.List[string] }
function Add-XmlLine([int]$Indent, [string]$Text) {
    $pad = ""
    if ($Indent -gt 0) { $pad = "  " * $Indent }
    $null = $script:XmlParts.Add($pad + $Text)
}
function Open-XmlBlock([int]$Indent, [string]$OpenTag, $Inner) {
    Add-XmlLine $Indent $OpenTag
    if ($null -ne $Inner) {
        $entries = $Inner
        if ($Inner -is [string]) {
            $entries = New-Object System.Collections.Generic.List[string]
            $null = $entries.Add([string]$Inner)
        }
        foreach ($entry in $entries) {
            if ($null -eq $entry) { continue }
            $text = [string]$entry
            $normalized = $text.Replace("`r`n", "`n").Replace("`r", "`n")
            if ($normalized.Length -gt 0 -and $normalized.EndsWith("`n")) { $normalized = $normalized.Substring(0, $normalized.Length - 1) }
            if ($normalized.Length -eq 0) { continue }
            foreach ($line in $normalized.Split(@("`n"), [StringSplitOptions]::None)) { Add-XmlLine ($Indent + 1) $line }
        }
    }
    $tagName = $OpenTag.Split(" ".ToCharArray())[0].TrimStart("<".ToCharArray())
    Add-XmlLine $Indent ("</" + $tagName + ">")
    Add-XmlLine $Indent "<hashTree>"
}
function Close-XmlBlock([int]$Indent) { Add-XmlLine $Indent "</hashTree>" }
function Complete-Xml { return (($script:XmlParts -join "`n") + "`n") }

function ConvertTo-JsonString([string]$Text) {
    if ($null -eq $Text) { $Text = "" }
    $sb = New-Object System.Text.StringBuilder
    $null = $sb.Append('"')
    foreach ($char in $Text.ToCharArray()) {
        $code = [int]$char
        if ($char -eq '"') { $null = $sb.Append('\"') }
        elseif ($char -eq '\') { $null = $sb.Append('\\') }
        elseif ($char -eq "`n") { $null = $sb.Append('\n') }
        elseif ($char -eq "`r") { $null = $sb.Append('\r') }
        elseif ($char -eq "`t") { $null = $sb.Append('\t') }
        elseif ($code -lt 32) { $null = $sb.Append(('\u{0:x4}' -f $code)) }
        else { $null = $sb.Append($char) }
    }
    $null = $sb.Append('"')
    return $sb.ToString()
}
function Format-JsonNumber($Value) {
    if ($Value -is [int] -or $Value -is [long]) { return ([int64]$Value).ToString([System.Globalization.CultureInfo]::InvariantCulture) }
    $rounded = [Math]::Round([double]$Value, 4)
    $text = $rounded.ToString("0.######", [System.Globalization.CultureInfo]::InvariantCulture)
    if ($text.IndexOf(".") -lt 0) { return ($text + ".0") }
    return $text
}
function Format-JsonValue($Value, [int]$Level) {
    $pad = ""
    $padIn = "  "
    if ($Level -gt 0) { $pad = "  " * $Level }
    if ($Level -ge 0) { $padIn = "  " * ($Level + 1) }
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) { if ($Value) { return "true" }; return "false" }
    if ($Value -is [int] -or $Value -is [long]) { return (Format-JsonNumber $Value) }
    if (Test-Number $Value) { return (Format-JsonNumber $Value) }
    if ($Value -is [string]) { return (ConvertTo-JsonString $Value) }
    if ($Value -is [YamlMap]) {
        if ($Value.Count -eq 0) { return "{}" }
        $sb = New-Object System.Text.StringBuilder
        $null = $sb.Append("{`n")
        $index = 0
        foreach ($pair in $Value) {
            if ($index -gt 0) { $null = $sb.Append(",`n") }
            $null = $sb.Append($padIn)
            $null = $sb.Append((ConvertTo-JsonString ([string]$pair.Key)))
            $null = $sb.Append(": ")
            $null = $sb.Append((Format-JsonValue $pair.Value ($Level + 1)))
            $index++
        }
        $null = $sb.Append("`n")
        $null = $sb.Append($pad)
        $null = $sb.Append("}")
        return $sb.ToString()
    }
    if ($Value -is [System.Collections.IList]) {
        if ($Value.Count -eq 0) { return "[]" }
        $sb = New-Object System.Text.StringBuilder
        $null = $sb.Append("[`n")
        $index = 0
        foreach ($item in $Value) {
            if ($index -gt 0) { $null = $sb.Append(",`n") }
            $null = $sb.Append($padIn)
            $null = $sb.Append((Format-JsonValue $item ($Level + 1)))
            $index++
        }
        $null = $sb.Append("`n")
        $null = $sb.Append($pad)
        $null = $sb.Append("]")
        return $sb.ToString()
    }
    return (ConvertTo-JsonString ([string]$Value))
}
function Escape-Form([string]$Text) {
    if ($null -eq $Text) { return "" }
    return [Uri]::EscapeDataString($Text).Replace("%20", "+")
}
function Escape-Segment([string]$Text) {
    if ($null -eq $Text) { return "" }
    return [Uri]::EscapeDataString($Text)
}
function ConvertTo-Slug([string]$Value) {
    if ($null -eq $Value) { return "field" }
    $lower = $Value.Trim().ToLowerInvariant()
    $cleaned = [regex]::Replace($lower, "[^A-Za-z0-9_.-]+", "-").Trim("-")
    if ($cleaned.Length -gt 48) { $cleaned = $cleaned.Substring(0, 48).Trim("-") }
    if ($cleaned.Length -eq 0) { return "field" }
    return $cleaned
}
function Next-Inclusive($Rng, [int]$Lower, [int]$Upper) {
    if ($Upper -le $Lower) { return $Lower }
    if ($Upper -ge [int]::MaxValue) { return $Rng.Next($Lower, [int]::MaxValue) }
    return $Rng.Next($Lower, $Upper + 1)
}
function Get-MethodIndex([string]$Method) {
    $lower = ""
    if ($null -ne $Method) { $lower = $Method.ToLowerInvariant() }
    for ($index = 0; $index -lt $script:HttpMethods.Count; $index++) {
        if ([string]::Equals($script:HttpMethods[$index], $lower, [StringComparison]::Ordinal)) { return $index }
    }
    return 99
}

function New-StringSchema {
    $map = New-Map
    $map.Set("type", "string")
    return ,$map
}
function New-Index([string]$Path, $Root) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $docs = New-Object "System.Collections.Generic.Dictionary[string,object]"
    $docs[$full] = $Root
    $version = ""
    if ($Root -is [YamlMap]) {
        $version = [string]$Root.Get("openapi")
        if ([string]::IsNullOrEmpty($version)) { $version = [string]$Root.Get("swagger") }
    }
    return (New-Object PSObject -Property @{ Path = $full; Root = $Root; Docs = $docs; Version = $version })
}
function ConvertFrom-PointerToken([string]$Token) {
    return [Uri]::UnescapeDataString($Token).Replace("~1", "/").Replace("~0", "~")
}
function Get-Pointer($Document, [string]$Pointer) {
    if ($Pointer -eq "" -or $Pointer -eq "#") {
        if ($null -eq $Document) { return $null }
        return ,$Document
    }
    if (-not $Pointer.StartsWith("#/")) { Fail "対応していない `$ref です: $Pointer" 3 }
    $current = $Document
    foreach ($rawPart in $Pointer.Substring(2).Split("/")) {
        $part = ConvertFrom-PointerToken $rawPart
        if ($current -is [YamlMap] -and $current.Has($part)) { $current = $current.Get($part); continue }
        if (($current -is [YamlSeq]) -and $part -match '^\d+$') {
            $index = [int]$part
            if ($index -ge 0 -and $index -lt $current.Count) { $current = $current[$index]; continue }
        }
        Fail "`$ref の参照先がありません: $Pointer" 3
    }
    if ($null -eq $current) { return $null }
    return ,$current
}
function Resolve-Ref($Index, [string]$BaseFile, [string]$Ref) {
    if ($Ref.StartsWith("#")) {
        $target = Get-Pointer $Index.Docs[$BaseFile] $Ref
        return (New-Object PSObject -Property @{ Target = $target; Base = $BaseFile })
    }
    $hash = $Ref.IndexOf("#")
    $filePart = $Ref
    $fragment = ""
    $hasHash = $false
    if ($hash -ge 0) { $filePart = $Ref.Substring(0, $hash); $fragment = $Ref.Substring($hash + 1); $hasHash = $true }
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetDirectoryName($BaseFile)) $filePart))
    if (-not $Index.Docs.ContainsKey($targetPath)) {
        if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) { Fail "`$ref のファイルが見つかりません: $Ref" 3 }
        $loaded = Import-SpecFile $targetPath
        $Index.Docs[$targetPath] = $loaded
    }
    $pointer = "#"
    if ($hasHash -and $fragment.Length -gt 0) { $pointer = "#" + $fragment }
    if ($pointer -eq "#") { return (New-Object PSObject -Property @{ Target = $Index.Docs[$targetPath]; Base = $targetPath }) }
    $target = Get-Pointer $Index.Docs[$targetPath] $pointer
    return (New-Object PSObject -Property @{ Target = $target; Base = $targetPath })
}
function Merge-Schema($Parts) {
    $merged = New-Map
    $properties = New-Map
    $required = New-Object System.Collections.Generic.List[string]
    $sawProperties = $false
    $sawRequired = $false
    foreach ($part in $Parts) {
        if ($part -isnot [YamlMap]) { continue }
        $partProperties = $part.Get("properties")
        if ($partProperties -is [YamlMap]) {
            $sawProperties = $true
            foreach ($pair in $partProperties) { $properties.Set($pair.Key, $pair.Value) }
        }
        $partRequired = $part.Get("required")
        if ($partRequired -is [YamlSeq] -or ($partRequired -is [System.Collections.IList] -and $partRequired -isnot [string])) {
            $sawRequired = $true
            foreach ($name in $partRequired) {
                $text = [string]$name
                $found = $false
                foreach ($existing in $required) { if ([string]::Equals($existing, $text, [StringComparison]::Ordinal)) { $found = $true; break } }
                if (-not $found) { $null = $required.Add($text) }
            }
        }
        foreach ($pair in $part) {
            if ($pair.Key -eq "properties" -or $pair.Key -eq "required" -or $pair.Key -eq "allOf") { continue }
            if (-not $merged.Has($pair.Key)) { $merged.Set($pair.Key, $pair.Value); continue }
            $existing = $merged.Get($pair.Key)
            if (($pair.Key -eq "minLength" -or $pair.Key -eq "minimum") -and (Test-Number $pair.Value) -and (Test-Number $existing)) {
                if (([double]$pair.Value) -gt ([double]$existing)) { $merged.Set($pair.Key, $pair.Value) }
            } elseif (($pair.Key -eq "maxLength" -or $pair.Key -eq "maximum") -and (Test-Number $pair.Value) -and (Test-Number $existing)) {
                if (([double]$pair.Value) -lt ([double]$existing)) { $merged.Set($pair.Key, $pair.Value) }
            } elseif ($pair.Key -eq "nullable") {
                $merged.Set($pair.Key, (Test-Truthy $existing -or (Test-Truthy $pair.Value)))
            } elseif ($pair.Key -eq "enum" -and ($pair.Value -is [System.Collections.IList]) -and ($existing -is [System.Collections.IList]) -and $pair.Value -isnot [string] -and $existing -isnot [string]) {
                $allowed = New-Seq
                foreach ($item in $existing) {
                    foreach ($candidate in $pair.Value) { if (Test-Same $item $candidate) { $null = $allowed.Add($item); break } }
                }
                if ($allowed.Count -eq 0) { $merged.Set($pair.Key, $pair.Value) } else { $merged.Set($pair.Key, $allowed) }
            } else { $merged.Set($pair.Key, $pair.Value) }
        }
    }
    if ($sawProperties -and $properties.Count -gt 0) { $merged.Set("properties", $properties) }
    if ($sawRequired -and $required.Count -gt 0) {
        $seq = New-Seq
        foreach ($name in $required) { $null = $seq.Add($name) }
        $merged.Set("required", $seq)
    }
    return ,$merged
}
function Invoke-Deref($Schema, $Index, [string]$Base, $Stack) {
    if ($Schema -isnot [YamlMap]) { return ,(New-StringSchema) }
    if ($Schema.Has('$ref')) {
        $ref = [string]$Schema.Get('$ref')
        $marker = $Base + "::" + $ref
        if ($null -ne $Stack) { foreach ($seen in $Stack) { if ([string]::Equals([string]$seen, $marker, [StringComparison]::Ordinal)) { return ,(New-StringSchema) } } }
        $resolvedRef = Resolve-Ref $Index $Base $ref
        $next = New-Object System.Collections.Generic.List[string]
        if ($null -ne $Stack) { foreach ($seen in $Stack) { if ($null -ne $seen -and [string]$seen -ne "") { $null = $next.Add([string]$seen) } } }
        $null = $next.Add($marker)
        $resolved = Invoke-Deref $resolvedRef.Target $Index $resolvedRef.Base $next
        if ($Index.Version.StartsWith("3.1")) {
            $overlay = New-Map
            foreach ($pair in $Schema) { if ($pair.Key -ne '$ref') { $overlay.Set($pair.Key, $pair.Value) } }
            if ($overlay.Count -gt 0) {
                $overlayResolved = Invoke-Deref $overlay $Index $Base $next
                $bundle = New-Object System.Collections.Generic.List[object]
                $null = $bundle.Add($resolved)
                $null = $bundle.Add($overlayResolved)
                $mergedOverlay = Merge-Schema $bundle
                return ,$mergedOverlay
            }
        }
        return ,$resolved
    }
    $allOf = $Schema.Get("allOf")
    if (($allOf -is [YamlSeq] -or ($allOf -is [System.Collections.IList] -and $allOf -isnot [string])) -and $allOf.Count -gt 0) {
        $parts = New-Object System.Collections.Generic.List[object]
        foreach ($part in $allOf) { $null = $parts.Add((Invoke-Deref $part $Index $Base $Stack)) }
        $siblings = New-Map
        foreach ($pair in $Schema) { if ($pair.Key -ne "allOf") { $siblings.Set($pair.Key, $pair.Value) } }
        if ($siblings.Count -gt 0) { $null = $parts.Add((Invoke-Deref $siblings $Index $Base $Stack)) }
        $mergedAll = Merge-Schema $parts
        return ,$mergedAll
    }
    return ,$Schema
}
function Get-SchemaTypes($Schema) {
    $types = New-Object System.Collections.Generic.List[string]
    if ($Schema -isnot [YamlMap]) { return ,$types }
    $raw = $Schema.Get("type")
    if ($raw -is [string]) { $null = $types.Add($raw); return ,$types }
    if ($raw -is [System.Collections.IList] -and $raw -isnot [string]) { foreach ($item in $raw) { $null = $types.Add([string]$item) } }
    return ,$types
}
function New-Value($Schema, $Index, [string]$Base, $Rng, [int]$Depth, [bool]$AllowNull, [bool]$RequiredOnly, $Files, [string]$FilePrefix) {
    $Schema = Invoke-Deref $Schema $Index $Base @()
    if ($Depth -gt 6) {
        $kind = Get-SchemaTypes $Schema
        foreach ($item in $kind) { if ($item -eq "array") { return ,(New-Seq) } }
        if ($Schema.Has("properties") -or $Schema.Has("additionalProperties")) { return ,(New-Map) }
        foreach ($item in $kind) { if ($item -eq "object") { return ,(New-Map) } }
        return "depth-limit"
    }
    if ($Schema.Has("const")) {
        $const = $Schema.Get("const")
        if ($null -eq $const) { return $null }
        return ,$const
    }
    $enum = $Schema.Get("enum")
    if (($enum -is [YamlSeq] -or ($enum -is [System.Collections.IList] -and $enum -isnot [string])) -and $enum.Count -gt 0) {
        $chosen = $enum[$Rng.Next(0, $enum.Count)]
        if ($null -eq $chosen) { return $null }
        return ,$chosen
    }
    foreach ($key in @("oneOf", "anyOf")) {
        $options = $Schema.Get($key)
        if (($options -is [YamlSeq] -or ($options -is [System.Collections.IList] -and $options -isnot [string])) -and $options.Count -gt 0) {
            $chosen = New-Value $options[$Rng.Next(0, $options.Count)] $Index $Base $Rng ($Depth + 1) $AllowNull $RequiredOnly $Files $FilePrefix
            if ($null -eq $chosen) { return $null }
            return ,$chosen
        }
    }
    $types = Get-SchemaTypes $Schema
    $nullable = (Test-True $Schema.Get("nullable"))
    foreach ($item in $types) { if ($item -eq "null") { $nullable = $true } }
    $concrete = New-Object System.Collections.Generic.List[string]
    foreach ($item in $types) { if ($item -ne "null") { $null = $concrete.Add($item) } }
    if ($nullable -and $AllowNull -and ($Rng.NextDouble() -lt 0.15)) { return $null }
    if ($concrete.Count -eq 0) {
        if ($Schema.Has("properties") -or $Schema.Has("additionalProperties")) { $null = $concrete.Add("object") }
        elseif ($Schema.Has("items") -or $Schema.Has("prefixItems")) { $null = $concrete.Add("array") }
        elseif ($Schema.Has("default")) {
            $fallback = $Schema.Get("default")
            if ($null -eq $fallback) { return $null }
            return ,$fallback
        } elseif ($Schema.Has("example")) {
            $fallback = $Schema.Get("example")
            if ($null -eq $fallback) { return $null }
            return ,$fallback
        }
        else { $null = $concrete.Add("string") }
    }
    $kind = $concrete[0]
    if ($concrete.Count -gt 1) { $kind = $concrete[$Rng.Next(0, $concrete.Count)] }
    if ($kind -eq "object") { return ,(New-ObjectValue $Schema $Index $Base $Rng $Depth $RequiredOnly $Files $FilePrefix) }
    if ($kind -eq "array") { return ,(New-ArrayValue $Schema $Index $Base $Rng $Depth $RequiredOnly $Files $FilePrefix) }
    if ($kind -eq "integer") { return (New-IntegerValue $Schema $Rng) }
    if ($kind -eq "number") { return (New-NumberValue $Schema $Rng) }
    if ($kind -eq "boolean") { return [bool](Next-Inclusive $Rng 0 1) }
    return (New-StringValue $Schema $Rng)
}
function New-ObjectValue($Schema, $Index, [string]$Base, $Rng, [int]$Depth, [bool]$RequiredOnly, $Files, [string]$FilePrefix) {
    $properties = $Schema.Get("properties")
    if ($properties -isnot [YamlMap]) { $properties = New-Map }
    $requiredSet = New-Object "System.Collections.Generic.HashSet[string]"
    $requiredRaw = $Schema.Get("required")
    if ($requiredRaw -is [System.Collections.IList] -and $requiredRaw -isnot [string]) { foreach ($item in $requiredRaw) { $null = $requiredSet.Add([string]$item) } }
    $result = New-Map
    foreach ($pair in $properties) {
        $include = (-not $RequiredOnly) -or $requiredSet.Contains([string]$pair.Key)
        if (-not $include) { continue }
        $resolved = Invoke-Deref $pair.Value $Index $Base @()
        if (Test-True $resolved.Get("readOnly")) { continue }
        $allow = -not $requiredSet.Contains([string]$pair.Key)
        $result.Set([string]$pair.Key, (New-Value $pair.Value $Index $Base $Rng ($Depth + 1) $allow $RequiredOnly $Files ($FilePrefix + "-" + $pair.Key)))
    }
    $minimum = $Schema.Get("minProperties")
    $additional = $Schema.Get("additionalProperties")
    if ((Test-Number $minimum) -and $result.Count -lt [int]$minimum -and $additional -is [YamlMap]) {
        $nextIndex = 1
        while ($result.Count -lt [int]$minimum) {
            $key = "extra$nextIndex"
            $nextIndex++
            $result.Set($key, (New-Value $additional $Index $Base $Rng ($Depth + 1) $false $RequiredOnly $Files ($FilePrefix + "-" + $key)))
        }
    }
    if ($result.Count -eq 0 -and $additional -is [YamlMap]) {
        $result.Set("extra1", (New-Value $additional $Index $Base $Rng ($Depth + 1) $false $RequiredOnly $Files ($FilePrefix + "-extra1")))
    }
    return ,$result
}
function New-ArrayValue($Schema, $Index, [string]$Base, $Rng, [int]$Depth, [bool]$RequiredOnly, $Files, [string]$FilePrefix) {
    $minimum = 1
    if (Test-Number $Schema.Get("minItems")) { $minimum = [int]$Schema.Get("minItems") }
    $stated = [Math]::Max($minimum, 2)
    if (Test-Number $Schema.Get("maxItems")) { $stated = [int]$Schema.Get("maxItems") }
    $maximum = [Math]::Max($minimum, [Math]::Min($stated, [Math]::Max($minimum, 2)))
    $count = Next-Inclusive $Rng $minimum $maximum
    $prefix = $Schema.Get("prefixItems")
    $itemSchema = $Schema.Get("items")
    if ($null -eq $itemSchema) { $itemSchema = New-StringSchema }
    $values = New-Seq
    $seen = New-Object "System.Collections.Generic.HashSet[string]"
    for ($position = 0; $position -lt $count; $position++) {
        $chosen = $itemSchema
        if (($prefix -is [System.Collections.IList]) -and $prefix -isnot [string] -and $position -lt $prefix.Count) { $chosen = $prefix[$position] }
        for ($attempt = 0; $attempt -lt 8; $attempt++) {
            $value = New-Value $chosen $Index $Base $Rng ($Depth + 1) $false $RequiredOnly $Files ($FilePrefix + "-" + $position)
            $marker = Format-JsonValue $value 0
            $unique = Test-True $Schema.Get("uniqueItems")
            if ((-not $unique) -or (-not $seen.Contains($marker))) {
                if ($unique) { $null = $seen.Add($marker) }
                $null = $values.Add($value)
                break
            }
        }
    }
    return ,$values
}
function Convert-Bound($Value, [string]$Direction) {
    $number = [double]$Value
    if ($Direction -eq "ceil") { return [int][Math]::Ceiling($number) }
    return [int][Math]::Floor($number)
}
function Get-IntegerBounds($Schema) {
    $lower = $Schema.Get("minimum")
    $upper = $Schema.Get("maximum")
    $exclusiveLower = $Schema.Get("exclusiveMinimum")
    $exclusiveUpper = $Schema.Get("exclusiveMaximum")
    if ($exclusiveLower -is [bool]) {
        if ($exclusiveLower -and (Test-Number $lower)) { $lower = (Convert-Bound $lower "floor") + 1 }
        elseif (Test-Number $lower) { $lower = Convert-Bound $lower "ceil" }
        else { $lower = $null }
    } elseif (Test-Number $exclusiveLower) { $lower = (Convert-Bound $exclusiveLower "floor") + 1 }
    elseif (Test-Number $lower) { $lower = Convert-Bound $lower "ceil" }
    else { $lower = $null }
    if ($exclusiveUpper -is [bool]) {
        if ($exclusiveUpper -and (Test-Number $upper)) { $upper = (Convert-Bound $upper "ceil") - 1 }
        elseif (Test-Number $upper) { $upper = Convert-Bound $upper "floor" }
        else { $upper = $null }
    } elseif (Test-Number $exclusiveUpper) { $upper = (Convert-Bound $exclusiveUpper "ceil") - 1 }
    elseif (Test-Number $upper) { $upper = Convert-Bound $upper "floor" }
    else { $upper = $null }
    if ($null -eq $lower -and $null -eq $upper) { return (New-Object PSObject -Property @{ Lower = 0; Upper = 1000 }) }
    if ($null -eq $lower) {
        $lower = 0
        if ($upper -lt 0) { $lower = $upper - 1000 }
        if ($lower -gt $upper) { $lower = $upper }
    }
    if ($null -eq $upper) { $upper = $lower + 1000 }
    if ($lower -gt $upper) { $lower = $upper }
    return (New-Object PSObject -Property @{ Lower = [int]$lower; Upper = [int]$upper })
}
function Test-WholeStep($Value) {
    if (-not (Test-Number $Value)) { return $false }
    $number = [double]$Value
    if ($number -le 0) { return $false }
    return [Math]::Abs($number - [Math]::Round($number)) -lt 0.0000001
}
function New-IntegerValue($Schema, $Rng) {
    $bounds = Get-IntegerBounds $Schema
    $step = $Schema.Get("multipleOf")
    if (Test-WholeStep $step) {
        $stepInt = [int][Math]::Round([double]$step)
        $start = [int]([Math]::Ceiling($bounds.Lower / $stepInt) * $stepInt)
        $end = [int]([Math]::Floor($bounds.Upper / $stepInt) * $stepInt)
        if ($start -gt $end) { return [int]$bounds.Lower }
        $slots = [int](($end - $start) / $stepInt)
        return [int]($start + (Next-Inclusive $Rng 0 $slots) * $stepInt)
    }
    return [int](Next-Inclusive $Rng ([int]$bounds.Lower) ([int]$bounds.Upper))
}
function New-NumberValue($Schema, $Rng) {
    $bounds = Get-IntegerBounds $Schema
    if ($bounds.Lower -eq $bounds.Upper) { return [double]$bounds.Lower }
    $value = $bounds.Lower + ($Rng.NextDouble() * ($bounds.Upper - $bounds.Lower))
    $step = $Schema.Get("multipleOf")
    if ((Test-Number $step) -and ([double]$step) -gt 0) {
        $stepFloat = [double]$step
        $units = [Math]::Round(($value - $bounds.Lower) / $stepFloat)
        $value = $bounds.Lower + ($units * $stepFloat)
        if ($value -lt $bounds.Lower) { $value = [double]$bounds.Lower }
        if ($value -gt $bounds.Upper) { $value = [double]$bounds.Upper }
    }
    return [Math]::Round($value, 4)
}
function New-RandomText($Rng, [int]$Length) {
    if ($Length -le 0) { return "" }
    $chars = New-Object char[] $Length
    for ($index = 0; $index -lt $Length; $index++) { $chars[$index] = $script:Alphabet[$Rng.Next(0, $script:Alphabet.Length)] }
    return -join $chars
}
function New-SeededUuid($Rng) {
    $hex = "0123456789abcdef".ToCharArray()
    $chars = New-Object char[] 32
    for ($index = 0; $index -lt 32; $index++) { $chars[$index] = $hex[$Rng.Next(0, 16)] }
    $chars[12] = "4"
    $variant = "89ab".ToCharArray()
    $chars[16] = $variant[$Rng.Next(0, 4)]
    $raw = -join $chars
    return $raw.Substring(0, 8) + "-" + $raw.Substring(8, 4) + "-" + $raw.Substring(12, 4) + "-" + $raw.Substring(16, 4) + "-" + $raw.Substring(20, 12)
}
function New-FormatString([string]$Format, $Rng, [int]$Minimum, [int]$Maximum) {
    if ($Format -eq "date") { return ("{0:0000}-{1:00}-{2:00}" -f (Next-Inclusive $Rng 2020 2030), (Next-Inclusive $Rng 1 12), (Next-Inclusive $Rng 1 28)) }
    if ($Format -eq "date-time" -or $Format -eq "datetime") {
        return ("{0:0000}-{1:00}-{2:00}T{3:00}:{4:00}:{5:00}Z" -f (Next-Inclusive $Rng 2020 2030), (Next-Inclusive $Rng 1 12), (Next-Inclusive $Rng 1 28), (Next-Inclusive $Rng 0 23), (Next-Inclusive $Rng 0 59), (Next-Inclusive $Rng 0 59))
    }
    if ($Format -eq "email") { return ("user{0}@example.com" -f (Next-Inclusive $Rng 1000 999999)) }
    if ($Format -eq "uuid") { return (New-SeededUuid $Rng) }
    if ($Format -eq "uri" -or $Format -eq "url" -or $Format -eq "uri-reference") { return ("https://example.com/r/" + (New-RandomText $Rng 8)) }
    if ($Format -eq "hostname" -or $Format -eq "idn-hostname") { return ("host-" + (New-RandomText $Rng 6) + ".example.com") }
    if ($Format -eq "ipv4" -or $Format -eq "ip") {
        $parts = @()
        for ($index = 0; $index -lt 4; $index++) { $parts += [string](Next-Inclusive $Rng 1 254) }
        return ($parts -join ".")
    }
    if ($Format -eq "ipv6") {
        $parts = @()
        for ($index = 0; $index -lt 8; $index++) { $parts += (Next-Inclusive $Rng 0 65535).ToString("x") }
        return ($parts -join ":")
    }
    if ($Format -eq "byte") {
        $bytes = New-Object byte[] 12
        $Rng.NextBytes($bytes)
        return [Convert]::ToBase64String($bytes)
    }
    if ($Format -eq "password" -or $Format -eq "binary") { return (New-RandomText $Rng ([Math]::Max($Minimum, 12))) }
    $length = $Minimum
    if ($Minimum -le 0) { $length = [Math]::Min(8, $Maximum) }
    $cap = $length
    if ($Maximum -gt 0) { $cap = $Maximum }
    $length = [Math]::Max(1, [Math]::Min($length, $cap))
    return (New-RandomText $Rng $length)
}
function Stop-Pattern { throw [System.InvalidOperationException]::new("PATTERN_UNSUPPORTED") }
function New-PatternState([string]$Pattern) {
    $state = New-Object PSObject -Property @{ Pattern = $Pattern; Position = 0 }
    if ($Pattern.StartsWith("^")) { $state.Position = 1 }
    return $state
}
function Pattern-Peek($State) {
    if ($State.Position -ge $State.Pattern.Length) { return "" }
    return $State.Pattern.Substring($State.Position, 1)
}
function Pattern-Done($State) { return $State.Position -ge $State.Pattern.Length }
function New-Pat([string]$Kind) {
    $node = New-Object PatNode
    $node.Kind = $Kind
    return $node
}
function Parse-Alt($State) {
    $node = New-Pat "alt"
    $null = $node.Children.Add((Parse-Concat $State))
    while ((Pattern-Peek $State) -eq "|") {
        $State.Position++
        $null = $node.Children.Add((Parse-Concat $State))
    }
    $result = $node.Children[0]
    if ($node.Children.Count -gt 1) { $result = $node }
    if ((Pattern-Peek $State) -eq '$' -and $State.Position -eq ($State.Pattern.Length - 1)) { $State.Position++ }
    return $result
}
function Parse-Concat($State) {
    $node = New-Pat "cat"
    while (-not (Pattern-Done $State) -and (Pattern-Peek $State) -ne ")" -and (Pattern-Peek $State) -ne "|") {
        if ((Pattern-Peek $State) -eq '$' -and $State.Position -eq ($State.Pattern.Length - 1)) { break }
        $null = $node.Children.Add((Parse-Repeat $State))
    }
    if ($node.Children.Count -eq 0) { $empty = New-Pat "lit"; $empty.Text = ""; return $empty }
    if ($node.Children.Count -eq 1) { return $node.Children[0] }
    return $node
}
function Parse-Repeat($State) {
    $atom = Parse-Atom $State
    if (Pattern-Done $State) { return $atom }
    $char = Pattern-Peek $State
    if ($char -eq "*") { $State.Position++; $node = New-Pat "rep"; $node.Lower = 0; $node.Upper = 4; $node.Child = $atom; return $node }
    if ($char -eq "+") { $State.Position++; $node = New-Pat "rep"; $node.Lower = 1; $node.Upper = 4; $node.Child = $atom; return $node }
    if ($char -eq "?") { $State.Position++; $node = New-Pat "rep"; $node.Lower = 0; $node.Upper = 1; $node.Child = $atom; return $node }
    if ($char -eq "{") {
        $brace = Parse-Brace $State
        $node = New-Pat "rep"
        $node.Lower = $brace.Lower
        $node.Upper = [Math]::Min($brace.Upper, [Math]::Max($brace.Lower, 12))
        $node.Child = $atom
        return $node
    }
    return $atom
}
function Parse-Brace($State) {
    $State.Position++
    $start = $State.Position
    while (-not (Pattern-Done $State) -and (Pattern-Peek $State) -ne "}") { $State.Position++ }
    if ((Pattern-Done $State) -or (Pattern-Peek $State) -ne "}") { Stop-Pattern }
    $body = $State.Pattern.Substring($start, $State.Position - $start)
    $State.Position++
    if ($body -notmatch '^\d+(,\d*)?$') { Stop-Pattern }
    $node = New-Object PSObject -Property @{ Lower = 0; Upper = 0 }
    if ($body.IndexOf(",") -lt 0) { $node.Lower = [int]$body; $node.Upper = [int]$body; return $node }
    $split = $body.Split(@(","), 2, [StringSplitOptions]::None)
    $node.Lower = [int]$split[0]
    if ($split[1].Length -eq 0) { $node.Upper = $node.Lower + 4 } else { $node.Upper = [int]$split[1] }
    if ($node.Upper -lt $node.Lower) { Stop-Pattern }
    return $node
}
function Parse-Atom($State) {
    if (Pattern-Done $State) { Stop-Pattern }
    $char = Pattern-Peek $State
    if ($char -eq "(") {
        $State.Position++
        if ($State.Pattern.Substring($State.Position).StartsWith("?:")) { $State.Position += 2 }
        elseif ((Pattern-Peek $State) -eq "?") { Stop-Pattern }
        $node = Parse-Alt $State
        if ((Pattern-Peek $State) -ne ")") { Stop-Pattern }
        $State.Position++
        return $node
    }
    if ($char -eq "[") { $node = New-Pat "class"; $node.Text = Parse-Class $State; return $node }
    if ($char -eq "\") { $node = New-Pat "class"; $node.Text = Parse-Escape $State; return $node }
    if ($char -eq ".") { $State.Position++; $node = New-Pat "class"; $node.Text = $script:Alphabet; return $node }
    if ("*+?{)|".Contains($char)) { Stop-Pattern }
    $State.Position++
    $node = New-Pat "lit"
    $node.Text = $char
    return $node
}
function Add-ClassChars($Builder, [string]$Chars) { $null = $Builder.Append($Chars) }
function Parse-Class($State) {
    $State.Position++
    $negated = $false
    if ((Pattern-Peek $State) -eq "^") { $negated = $true; $State.Position++ }
    $builder = New-Object System.Text.StringBuilder
    while (-not (Pattern-Done $State) -and (Pattern-Peek $State) -ne "]") {
        if ((Pattern-Peek $State) -eq "\") { Add-ClassChars $builder (Parse-Escape $State); continue }
        $start = Pattern-Peek $State
        $State.Position++
        if ((Pattern-Peek $State) -eq "-" -and ($State.Position + 1) -lt $State.Pattern.Length -and $State.Pattern.Substring($State.Position + 1, 1) -ne "]") {
            $State.Position++
            $end = Pattern-Peek $State
            $State.Position++
            $startCode = [int][char]$start
            $endCode = [int][char]$end
            if ($startCode -le $endCode) { for ($code = $startCode; $code -le $endCode; $code++) { $null = $builder.Append([char]$code) } }
        } else { $null = $builder.Append($start) }
    }
    if ((Pattern-Peek $State) -ne "]") { Stop-Pattern }
    $State.Position++
    $safe = New-Object System.Text.StringBuilder
    foreach ($char in $builder.ToString().ToCharArray()) {
        if ($char -eq "`r" -or $char -eq "`n" -or $char -eq "`t") { continue }
        if ([char]::IsControl($char)) { continue }
        $null = $safe.Append($char)
    }
    $text = $safe.ToString()
    if ($negated) {
        $universe = New-Object System.Text.StringBuilder
        foreach ($char in $script:Alphabet.ToCharArray()) { if ($text.IndexOf($char) -lt 0) { $null = $universe.Append($char) } }
        $text = $universe.ToString()
    }
    if ($text.Length -eq 0) { Stop-Pattern }
    if ($text.Length -gt 80) { return $text.Substring(0, 80) }
    return $text
}
function Parse-Escape($State) {
    $State.Position++
    if (Pattern-Done $State) { Stop-Pattern }
    $char = Pattern-Peek $State
    $State.Position++
    if ($char -eq "d") { return "0123456789" }
    if ($char -eq "w") { return ($script:Alphabet + "_") }
    if ($char -eq "s") { return " " }
    if ($char -eq "D" -or $char -eq "W" -or $char -eq "S") { return $script:Letters }
    if ($char -eq "n" -or $char -eq "t" -or $char -eq "r") { return " " }
    if ([char]::IsDigit($char, 0)) { Stop-Pattern }
    return $char
}
function Render-Pattern($Node, $Rng, $Pieces, $Budget) {
    if ($Budget[0] -le 0) { return }
    if ($Node.Kind -eq "lit") {
        $text = [string]$Node.Text
        $take = $text
        if ($take.Length -gt $Budget[0]) { $take = $take.Substring(0, $Budget[0]) }
        $null = $Pieces.Add($take)
        $Budget[0] -= $text.Length
        return
    }
    if ($Node.Kind -eq "class") {
        $choices = [string]$Node.Text
        $null = $Pieces.Add($choices.Substring($Rng.Next(0, $choices.Length), 1))
        $Budget[0] -= 1
        return
    }
    if ($Node.Kind -eq "cat") { foreach ($child in $Node.Children) { Render-Pattern $child $Rng $Pieces $Budget }; return }
    if ($Node.Kind -eq "alt") { Render-Pattern $Node.Children[$Rng.Next(0, $Node.Children.Count)] $Rng $Pieces $Budget; return }
    if ($Node.Kind -eq "rep") {
        $count = Next-Inclusive $Rng $Node.Lower $Node.Upper
        for ($index = 0; $index -lt $count; $index++) {
            if ($Budget[0] -le 0) { break }
            Render-Pattern $Node.Child $Rng $Pieces $Budget
        }
        return
    }
    Stop-Pattern
}
function New-PatternText($Rng, [string]$Pattern) {
    if ($Pattern.Length -gt 180) { return $null }
    try {
        $state = New-PatternState $Pattern
        $ast = Parse-Alt $state
        if ($state.Position -ne $Pattern.Length) { return $null }
        $pieces = New-StringList
        $budget = New-Object int[] 1
        $budget[0] = 40
        Render-Pattern $ast $Rng $pieces $budget
        return ($pieces -join "")
    } catch [System.InvalidOperationException] {
        if ($_.Exception.Message -eq "PATTERN_UNSUPPORTED") { return $null }
        throw
    } catch {
        $inner = $_.Exception
        while ($null -ne $inner) {
            if ($inner.Message -eq "PATTERN_UNSUPPORTED") { return $null }
            $inner = $inner.InnerException
        }
        throw
    }
}
function New-StringValue($Schema, $Rng) {
    $minimum = 0
    if (Test-Number $Schema.Get("minLength")) { $minimum = [int]$Schema.Get("minLength") }
    $hasMaximum = $Schema.Has("maxLength")
    $ceiling = [Math]::Max($minimum, 64)
    if ($hasMaximum) { $ceiling = [int]$Schema.Get("maxLength") }
    $maximum = [Math]::Max($minimum, [Math]::Min($ceiling, [Math]::Max($minimum, 64)))
    $pattern = $Schema.Get("pattern")
    if ($pattern -is [string]) {
        $generated = New-PatternText $Rng $pattern
        if ($null -ne $generated -and $generated.Length -ge $minimum -and $generated.Length -le $maximum) { return $generated }
    }
    $format = ""
    if ($Schema.Get("format") -is [string]) { $format = [string]$Schema.Get("format") }
    $value = New-FormatString $format $Rng $minimum $maximum
    if (-not $hasMaximum) {
        if ($value.Length -lt $minimum) { return ($value + (New-RandomText $Rng ($minimum - $value.Length))) }
        return $value
    }
    if ($value.Length -lt $minimum) { $value = $value + (New-RandomText $Rng ($minimum - $value.Length)) }
    if ($value.Length -gt $maximum) { $value = $value.Substring(0, $maximum) }
    if ($value -eq "" -and $minimum -eq 0 -and $maximum -eq 0) { return "" }
    if ($value -eq "") { $value = New-RandomText $Rng ([Math]::Max(1, $minimum)) }
    return $value
}

function Follow-Node($Value, $Index, [string]$Base) {
    $current = $Value
    $currentBase = $Base
    $seen = New-Object "System.Collections.Generic.HashSet[string]"
    while ($current -is [YamlMap] -and $current.Has('$ref')) {
        $ref = [string]$current.Get('$ref')
        $marker = $currentBase + "::" + $ref
        if ($seen.Contains($marker)) { break }
        $null = $seen.Add($marker)
        $resolved = Resolve-Ref $Index $currentBase $ref
        $currentBase = $resolved.Base
        if ($Index.Version.StartsWith("3.1") -and $resolved.Target -is [YamlMap]) {
            $overlay = New-Map
            foreach ($pair in $current) { if ($pair.Key -ne '$ref') { $overlay.Set($pair.Key, $pair.Value) } }
            if ($overlay.Count -gt 0) {
                $merged = New-Map
                foreach ($pair in $resolved.Target) { $merged.Set($pair.Key, $pair.Value) }
                foreach ($pair in $overlay) { $merged.Set($pair.Key, $pair.Value) }
                $current = $merged
                continue
            }
        }
        $current = $resolved.Target
    }
    if ($null -eq $current) { return $null }
    return ,$current
}
function Get-ServerPath($Server) {
    $url = [string]$Server.Get("url")
    $variables = $Server.Get("variables")
    if ($variables -is [YamlMap]) {
        foreach ($pair in $variables) {
            $default = ""
            if ($pair.Value -is [YamlMap] -and $null -ne $pair.Value.Get("default")) { $default = [string]$pair.Value.Get("default") }
            $url = $url.Replace("{" + $pair.Key + "}", (Escape-Segment $default))
        }
    }
    if ($url.StartsWith("/")) { $path = $url } else {
        $uri = $null
        if ([Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uri)) { $path = $uri.AbsolutePath } else { $path = "" }
    }
    return $path.TrimEnd("/")
}
function Get-BasePath($Root, $Options) {
    if ($Options.BasePath -eq "none") { return "" }
    if ($Options.BasePath -ne "auto") {
        $text = $Options.BasePath.Trim()
        if ($text.Length -gt 0 -and -not $text.StartsWith("/")) { $text = "/" + $text }
        return $text.TrimEnd("/")
    }
    if ($Root.Has("swagger") -and $Root.Get("basePath") -is [string]) { return ([string]$Root.Get("basePath")).TrimEnd("/") }
    $servers = $Root.Get("servers")
    if ($servers -isnot [YamlSeq] -and -not ($servers -is [System.Collections.IList] -and $servers -isnot [string])) { return "" }
    if ($null -eq $servers -or $servers.Count -eq 0) { return "" }
    $index = [int]$Options.ServerIndex
    if ($index -lt 0 -or $index -ge $servers.Count -or $servers[$index] -isnot [YamlMap]) { Fail "server-index $index は servers の範囲外です。" 3 }
    return (Get-ServerPath $servers[$index])
}
function ConvertTo-ScalarText($Value) {
    if ($null -eq $Value) { return "" }
    if ($Value -is [string]) { return $Value }
    if ($Value -is [bool]) { if ($Value) { return "true" }; return "false" }
    if (Test-Number $Value) {
        if ($Value -is [int] -or $Value -is [long]) { return ([int64]$Value).ToString([System.Globalization.CultureInfo]::InvariantCulture) }
        return (Format-JsonNumber $Value)
    }
    if ($Value -is [YamlMap] -or ($Value -is [System.Collections.IList] -and $Value -isnot [string])) { return (Format-JsonValue $Value 0) }
    return [string]$Value
}
function Join-UrlPath([string]$Base, [string]$Path) {
    if (-not $Path.StartsWith("/")) { $Path = "/" + $Path }
    if ([string]::IsNullOrEmpty($Base)) { return $Path }
    return $Base.TrimEnd("/") + $Path
}
function Add-Query([string]$Path, $Query) {
    if ($null -eq $Query -or $Query.Count -eq 0) { return $Path }
    $parts = New-StringList
    foreach ($pair in $Query) { $null = $parts.Add((Escape-Form $pair.Name) + "=" + (Escape-Form $pair.Value)) }
    $joined = $parts -join "&"
    if ($Path.Contains("?")) { return $Path + "&" + $joined }
    return $Path + "?" + $joined
}
function Get-MediaBase([string]$Media) {
    $split = $Media.Split(@(";"), 2, [StringSplitOptions]::None)
    return $split[0].Trim()
}
function Choose-Content($Content) {
    $normalized = New-Object System.Collections.Generic.List[object]
    foreach ($pair in $Content) {
        $null = $normalized.Add((New-Object PSObject -Property @{ Base = (Get-MediaBase ([string]$pair.Key)); Value = $pair.Value }))
    }
    foreach ($preferred in $script:ContentPreference) {
        foreach ($item in $normalized) {
            if ([string]::Equals($item.Base, $preferred, [StringComparison]::Ordinal)) { return $item }
        }
    }
    foreach ($item in $normalized) { if ($item.Base.EndsWith("+json")) { return $item } }
    return $normalized[0]
}
function Add-ByteFile($Files, [string]$Name, [byte[]]$Payload) {
    $relative = "testdata/" + $Name
    $null = $Files.Add((New-Object ByteFile($relative, $Payload)))
    return $relative
}
function Test-BinaryProperty($Schema, [string]$Key, $Index) {
    $resolved = Invoke-Deref $Schema $Index $Index.Path @()
    $properties = $resolved.Get("properties")
    if ($properties -isnot [YamlMap]) { return $false }
    $property = $properties.Get($Key)
    if ($property -isnot [YamlMap]) { return $false }
    $property = Invoke-Deref $property $Index $Index.Path @()
    $format = [string]$property.Get("format")
    if ($format -eq "binary" -or $format -eq "byte") { return $true }
    return ([string]$property.Get("type") -eq "file")
}
function ConvertTo-XmlBody([string]$RootName, $Value) { return "<" + (ConvertTo-Slug $RootName) + ">`n" + (Format-XmlNodes $Value 1) + "</" + (ConvertTo-Slug $RootName) + ">`n" }
function Format-XmlNodes($Value, [int]$Indent) {
    $pad = "  " * $Indent
    if ($Value -is [YamlMap]) {
        $chunks = New-StringList
        foreach ($pair in $Value) {
            $name = ConvertTo-Slug ([string]$pair.Key)
            $null = $chunks.Add($pad + "<" + $name + ">`n" + (Format-XmlNodes $pair.Value ($Indent + 1)) + $pad + "</" + $name + ">`n")
        }
        return ($chunks -join "")
    }
    if ($Value -is [System.Collections.IList] -and $Value -isnot [string]) {
        $chunks = New-StringList
        foreach ($item in $Value) { $null = $chunks.Add($pad + "<item>`n" + (Format-XmlNodes $item ($Indent + 1)) + $pad + "</item>`n") }
        return ($chunks -join "")
    }
    return $pad + (ConvertTo-XmlText (ConvertTo-ScalarText $Value)) + "`n"
}
function Render-Form([string]$Media, $Fields, $FileArgs) {
    if ($Media -eq $script:MultipartContent) { return (New-Object PSObject -Property @{ Media = $Media; Body = ""; Fields = $Fields; FileArgs = $FileArgs }) }
    return (New-Object PSObject -Property @{ Media = $Media; Body = (Add-Query "" $Fields).TrimStart("?"); Fields = (New-TextList); FileArgs = (New-FileList) })
}
function Render-Body($Index, [string]$Media, $Schema, $Rng, [bool]$RequiredOnly, $Files, [string]$RawPath, $ExistingFields) {
    if ($Media -eq $script:FormContent -or $Media -eq $script:MultipartContent) {
        $value = New-Value $Schema $Index $Index.Path $Rng 0 $false $RequiredOnly $Files (ConvertTo-Slug $RawPath)
        $fields = New-TextList
        if ($null -ne $ExistingFields) { foreach ($pair in $ExistingFields) { if ($null -ne $pair) { Add-Text $fields $pair.Name $pair.Value } } }
        $fileArgs = New-FileList
        if ($value -is [YamlMap]) {
            foreach ($pair in $value) {
                if ($Media -eq $script:MultipartContent -and $pair.Value -is [string] -and (Test-BinaryProperty $Schema ([string]$pair.Key) $Index)) {
                    $relative = Add-ByteFile $Files ((ConvertTo-Slug $RawPath) + "-" + (ConvertTo-Slug ([string]$pair.Key)) + ".bin") ([System.Text.Encoding]::UTF8.GetBytes([string]$pair.Value))
                    $null = $fileArgs.Add((New-Object FileArg($relative, [string]$pair.Key, $script:BinaryContent)))
                } else { Add-Text $fields ([string]$pair.Key) (ConvertTo-ScalarText $pair.Value) }
            }
        } else { Add-Text $fields "value" (ConvertTo-ScalarText $value) }
        return (Render-Form $Media $fields $fileArgs)
    }
    $value = New-Value $Schema $Index $Index.Path $Rng 0 $false $RequiredOnly $Files (ConvertTo-Slug $RawPath)
    $emptyFields = New-TextList
    $emptyFiles = New-FileList
    if ($Media.EndsWith("+json") -or $Media -eq $script:JsonContent) {
        return (New-Object PSObject -Property @{ Media = $Media; Body = (Format-JsonValue $value 0); Fields = $emptyFields; FileArgs = $emptyFiles })
    }
    if ($Media -eq $script:XmlContent -or $Media.EndsWith("+xml")) {
        return (New-Object PSObject -Property @{ Media = $Media; Body = (ConvertTo-XmlBody "request" $value); Fields = $emptyFields; FileArgs = $emptyFiles })
    }
    return (New-Object PSObject -Property @{ Media = $Media; Body = (ConvertTo-ScalarText $value); Fields = $emptyFields; FileArgs = $emptyFiles })
}
function Get-Accept($Operation, $Index) {
    $responses = $Operation.Get("responses")
    if ($responses -isnot [YamlMap]) { $responses = New-Map }
    $ordered = New-StringList
    foreach ($key in @("200", "201", "202", "203")) { if ($responses.Has($key)) { $null = $ordered.Add($key) } }
    foreach ($pair in $responses) {
        $text = [string]$pair.Key
        if ($text.StartsWith("2")) {
            $found = $false
            foreach ($existing in $ordered) { if ($existing -eq $text) { $found = $true; break } }
            if (-not $found) { $null = $ordered.Add($text) }
        }
    }
    if ($responses.Has("default")) {
        $found = $false
        foreach ($existing in $ordered) { if ($existing -eq "default") { $found = $true; break } }
        if (-not $found) { $null = $ordered.Add("default") }
    }
    foreach ($key in $ordered) {
        $response = Follow-Node $responses.Get($key) $Index $Index.Path
        if ($response -is [YamlMap]) {
            $content = $response.Get("content")
            if ($content -is [YamlMap] -and $content.Count -gt 0) { return (Get-MediaBase ([string]$content[0].Key)) }
        }
    }
    foreach ($source in @($Operation.Get("produces"), $Index.Root.Get("produces"))) {
        if ($source -is [System.Collections.IList] -and $source -isnot [string] -and $source.Count -gt 0) { return (Get-MediaBase ([string]$source[0])) }
    }
    return $script:JsonContent
}
function ConvertTo-SwaggerSchema($Parameter) {
    $schema = New-Map
    foreach ($key in @("type", "format", "enum", "minimum", "maximum", "minLength", "maxLength", "pattern", "items", "default")) {
        if ($Parameter.Has($key)) { $schema.Set($key, $Parameter.Get($key)) }
    }
    if (-not $schema.Has("type")) { $schema.Set("type", "string") }
    return ,$schema
}
function Merge-Parameter($Index, $PathParameters, $OperationParameters) {
    $merged = New-Object System.Collections.Generic.List[object]
    $keys = New-Object System.Collections.Generic.List[string]
    $sources = New-Object System.Collections.Generic.List[object]
    if ($PathParameters -is [System.Collections.IList] -and $PathParameters -isnot [string]) { foreach ($item in $PathParameters) { $null = $sources.Add($item) } }
    if ($OperationParameters -is [System.Collections.IList] -and $OperationParameters -isnot [string]) { foreach ($item in $OperationParameters) { $null = $sources.Add($item) } }
    foreach ($raw in $sources) {
        $parameter = Follow-Node $raw $Index $Index.Path
        if ($parameter -isnot [YamlMap]) { continue }
        $key = [string]$parameter.Get("name") + "|" + [string]$parameter.Get("in")
        $found = -1
        for ($index = 0; $index -lt $keys.Count; $index++) { if ([string]::Equals($keys[$index], $key, [StringComparison]::Ordinal)) { $found = $index; break } }
        if ($found -ge 0) { $merged[$found] = $parameter } else { $null = $keys.Add($key); $null = $merged.Add($parameter) }
    }
    return ,$merged
}
function Build-RequestBody($Index, $Operation, [string]$Method, $SwaggerBody, [bool]$SwaggerBodyRequired, $FormFields, $Rng, [bool]$RequiredOnly, $Files, [string]$RawPath) {
    $empty = New-Object PSObject -Property @{ Media = ""; Body = ""; Fields = (New-TextList); FileArgs = (New-FileList) }
    $requestBody = $Operation.Get("requestBody")
    if ($requestBody -is [YamlMap] -or ($requestBody -is [YamlMap])) {
        $requestBody = Follow-Node $requestBody $Index $Index.Path
    }
    if ($requestBody -is [YamlMap]) {
        if ($RequiredOnly -and -not (Test-True $requestBody.Get("required"))) { return $empty }
        $content = $requestBody.Get("content")
        if ($content -is [YamlMap] -and $content.Count -gt 0) {
            $chosen = Choose-Content $content
            $schema = New-Map
            if ($chosen.Value -is [YamlMap] -and $chosen.Value.Get("schema") -is [YamlMap]) { $schema = $chosen.Value.Get("schema") }
            return (Render-Body $Index $chosen.Base $schema $Rng $RequiredOnly $Files $RawPath $FormFields)
        }
    }
    $consumes = $Operation.Get("consumes")
    if ($consumes -isnot [System.Collections.IList] -or $consumes -is [string]) { $consumes = $Index.Root.Get("consumes") }
    if ($null -ne $FormFields -and $FormFields.Count -gt 0) {
        $media = $script:FormContent
        if ($consumes -is [System.Collections.IList] -and $consumes -isnot [string]) {
            foreach ($candidate in $consumes) {
                if ($candidate -is [string] -and (Get-MediaBase $candidate) -eq $script:MultipartContent) { $media = $script:MultipartContent }
            }
        }
        return (Render-Form $media $FormFields (New-FileList))
    }
    if ($null -ne $SwaggerBody) {
        if ($RequiredOnly -and -not $SwaggerBodyRequired) { return $empty }
        $media = $script:JsonContent
        if ($consumes -is [System.Collections.IList] -and $consumes -isnot [string]) {
            foreach ($candidate in $consumes) {
                if ($candidate -is [string]) {
                    $base = Get-MediaBase ([string]$candidate)
                    foreach ($preferred in $script:ContentPreference) {
                        if ([string]::Equals($base, $preferred, [StringComparison]::Ordinal)) { $media = $base; break }
                    }
                    if ($media -ne $script:JsonContent) { break }
                }
            }
        }
        return (Render-Body $Index $media $SwaggerBody $Rng $RequiredOnly $Files $RawPath (New-TextList))
    }
    return $empty
}
function Get-SamplerName($Operation) {
    $pathOnly = [string]$Operation.Path
    $query = $pathOnly.IndexOf("?")
    if ($query -ge 0) { $pathOnly = $pathOnly.Substring(0, $query) }
    $label = $Operation.Method + " " + $pathOnly
    if (-not [string]::IsNullOrEmpty([string]$Operation.OperationId)) { $label = $label + " [" + $Operation.OperationId + "]" }
    if ($Operation.Deprecated) { $label = "[deprecated] " + $label }
    return $label
}
function Build-OneOperation($Index, [string]$RawPath, [string]$BasePath, [string]$Method, $PathItem, $PathParameters, $Operation, $Rng, [bool]$RequiredOnly, $Files) {
    $parameters = Merge-Parameter $Index $PathParameters $Operation.Get("parameters")
    $pathValues = New-Object "System.Collections.Generic.Dictionary[string,string]"
    $query = New-TextList
    $headers = New-TextList
    $cookies = New-TextList
    $formFields = New-TextList
    $swaggerBody = $null
    $swaggerBodyRequired = $false
    foreach ($parameter in $parameters) {
        $location = [string]$parameter.Get("in")
        $name = [string]$parameter.Get("name")
        if ([string]::IsNullOrEmpty($name)) { continue }
        $required = (Test-True $parameter.Get("required")) -or ($location -eq "path")
        if ($RequiredOnly -and -not $required -and $location -ne "path") { continue }
        if ($location -eq "body") { $swaggerBody = $parameter.Get("schema"); if ($null -eq $swaggerBody) { $swaggerBody = New-Map; $swaggerBody.Set("type", "object") }; $swaggerBodyRequired = $required; continue }
        if ($location -eq "formData") {
            $schema = $parameter.Get("schema")
            if ($schema -isnot [YamlMap]) { $schema = ConvertTo-SwaggerSchema $parameter }
            $value = New-Value $schema $Index $Index.Path $Rng 0 (-not $required) $RequiredOnly $Files $name
            Add-Text $formFields $name (ConvertTo-ScalarText $value)
            continue
        }
        $schema = $parameter.Get("schema")
        if ($schema -isnot [YamlMap]) { $schema = ConvertTo-SwaggerSchema $parameter }
        $value = New-Value $schema $Index $Index.Path $Rng 0 (-not $required) $RequiredOnly $Files $name
        if ($null -eq $value) { continue }
        $text = ConvertTo-ScalarText $value
        if ($location -eq "path") { $pathValues[$name] = $text }
        elseif ($location -eq "query") { Add-Text $query $name $text }
        elseif ($location -eq "header") { Add-Text $headers $name $text }
        elseif ($location -eq "cookie") { Add-Text $cookies $name $text }
    }
    $filteredHeaders = New-TextList
    foreach ($header in $headers) {
        $lower = $header.Name.ToLowerInvariant()
        if ($lower -eq "accept" -or $lower -eq "authorization" -or $lower -eq "x-api-key") { continue }
        Add-Text $filteredHeaders $header.Name $header.Value
    }
    $rendered = $RawPath
    foreach ($key in $pathValues.Keys) { $rendered = $rendered.Replace("{" + $key + "}", (Escape-Segment $pathValues[$key])) }
    $missing = [regex]::Matches($rendered, "\{([^}]+)\}")
    if ($missing.Count -gt 0) {
        $names = @()
        foreach ($item in $missing) { $names += $item.Groups[1].Value }
        Fail ($Method.ToUpperInvariant() + " " + $RawPath + " のパスパラメータが足りません: " + ($names -join ", ")) 3
    }
    $fullPath = Join-UrlPath $BasePath $rendered
    $body = Build-RequestBody $Index $Operation $Method $swaggerBody $swaggerBodyRequired $formFields $Rng $RequiredOnly $Files $RawPath
    if ($query.Count -gt 0 -and -not [string]::IsNullOrEmpty($body.Media)) {
        $fullPath = Add-Query $fullPath $query
        $query = New-TextList
    }
    $cookieHeader = ""
    if ($cookies.Count -gt 0) {
        $parts = New-StringList
        foreach ($cookie in $cookies) { $null = $parts.Add($cookie.Name + "=" + $cookie.Value) }
        $cookieHeader = $parts -join "; "
    }
    $tags = $Operation.Get("tags")
    $tag = "default"
    if ($tags -is [System.Collections.IList] -and $tags -isnot [string] -and $tags.Count -gt 0) { $tag = [string]$tags[0] }
    $summary = [string]$Operation.Get("summary")
    if ([string]::IsNullOrEmpty($summary)) { $summary = [string]$Operation.Get("description") }
    $built = New-Object PSObject -Property @{
        Method = $Method.ToUpperInvariant(); RawPath = $RawPath; Path = $fullPath; OperationId = [string]$Operation.Get("operationId")
        Tag = $tag; Deprecated = (Test-True $Operation.Get("deprecated")); Summary = $summary; Query = $query; Headers = $filteredHeaders
        CookieHeader = $cookieHeader; ContentType = [string]$body.Media; Body = [string]$body.Body; MultipartFields = $body.Fields
        Files = $body.FileArgs; Accept = (Get-Accept $Operation $Index); SamplerName = ""
    }
    $built.SamplerName = Get-SamplerName $built
    return $built
}
function Build-Operations($Index, $Options, $Rng) {
    $root = $Index.Root
    if ($root -isnot [YamlMap]) { Fail "OpenAPI文書の先頭はオブジェクトである必要があります。" 3 }
    $paths = $root.Get("paths")
    if ($paths -isnot [YamlMap] -or $paths.Count -eq 0) { Fail "paths にAPIが定義されていません。" 4 }
    $basePath = Get-BasePath $root $Options
    $methodFilter = New-Object "System.Collections.Generic.HashSet[string]"
    foreach ($item in $Options.Methods.Split(@(","), [StringSplitOptions]::RemoveEmptyEntries)) { $null = $methodFilter.Add($item.Trim().ToLowerInvariant()) }
    $files = New-ByteFileList
    $operations = New-Object System.Collections.Generic.List[object]
    foreach ($pathPair in $paths) {
        if ($pathPair.Key -isnot [string] -and -not ($pathPair.Key -is [string])) { continue }
        $rawPath = [string]$pathPair.Key
        $pathItem = Follow-Node $pathPair.Value $Index $Index.Path
        if ($pathItem -isnot [YamlMap]) { continue }
        $pathParameters = $pathItem.Get("parameters")
        foreach ($method in $script:HttpMethods) {
            if (-not $pathItem.Has($method)) { continue }
            if ($methodFilter.Count -gt 0 -and -not $methodFilter.Contains($method)) { continue }
            $operation = Follow-Node $pathItem.Get($method) $Index $Index.Path
            if ($operation -isnot [YamlMap]) { continue }
            if ((Test-True $operation.Get("deprecated")) -and $Options.SkipDeprecated) { continue }
            $null = $operations.Add((Build-OneOperation $Index $rawPath $basePath $method $pathItem $pathParameters $operation $Rng $Options.RequiredOnly $files))
        }
    }
    if ($operations.Count -eq 0) { Fail "条件に合うAPI operation がありません。" 4 }
    $sorted = New-Object System.Collections.Generic.List[object]
    foreach ($operation in $operations) {
        $placed = $false
        for ($index = 0; $index -lt $sorted.Count; $index++) {
            $other = $sorted[$index]
            $compared = [string]::CompareOrdinal($operation.Tag, $other.Tag)
            if ($compared -eq 0) { $compared = [string]::CompareOrdinal($operation.RawPath, $other.RawPath) }
            if ($compared -eq 0) { $compared = (Get-MethodIndex $operation.Method) - (Get-MethodIndex $other.Method) }
            if ($compared -lt 0) { $sorted.Insert($index, $operation); $placed = $true; break }
        }
        if (-not $placed) { $null = $sorted.Add($operation) }
    }
    $seen = New-Object "System.Collections.Generic.Dictionary[string,int]"
    foreach ($operation in $sorted) {
        $name = Get-SamplerName $operation
        $count = 1
        if ($seen.ContainsKey($name)) { $count = $seen[$name] + 1 }
        $seen[$name] = $count
        if ($count -gt 1) {
            if ([string]::IsNullOrEmpty([string]$operation.OperationId)) { $operation.OperationId = $operation.Method }
            $operation.OperationId = [string]$operation.OperationId + "-" + $count
            $name = Get-SamplerName $operation
        }
        $operation.SamplerName = $name
    }
    return (New-Object PSObject -Property @{ Operations = $sorted; Files = $files })
}

function Format-RawArguments([string]$Body) {
    return '<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"><elementProp name="" elementType="HTTPArgument">' + (Format-BoolProp "HTTPArgument.always_encode" $false) + (Format-StringProp "Argument.value" $Body) + (Format-StringProp "Argument.metadata" "=") + "</elementProp></collectionProp></elementProp>"
}
function Format-NamedArguments($Arguments) {
    $chunks = New-StringList
    $null = $chunks.Add('<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true"><collectionProp name="Arguments.arguments">')
    if ($null -ne $Arguments) {
        foreach ($pair in $Arguments) {
            $null = $chunks.Add('<elementProp name="' + (ConvertTo-XmlText $pair.Name) + '" elementType="HTTPArgument">')
            $null = $chunks.Add((Format-BoolProp "HTTPArgument.always_encode" $true))
            $null = $chunks.Add((Format-StringProp "Argument.value" $pair.Value))
            $null = $chunks.Add((Format-StringProp "Argument.metadata" "="))
            $null = $chunks.Add((Format-BoolProp "HTTPArgument.use_equals" $true))
            $null = $chunks.Add((Format-StringProp "Argument.name" $pair.Name))
            $null = $chunks.Add("</elementProp>")
        }
    }
    $null = $chunks.Add("</collectionProp></elementProp>")
    return ($chunks -join "")
}
function Format-FileArguments($Files) {
    $chunks = New-StringList
    $null = $chunks.Add('<elementProp name="HTTPsampler.Files" elementType="HTTPFileArgs"><collectionProp name="HTTPFileArgs.files">')
    foreach ($file in $Files) {
        $null = $chunks.Add('<elementProp name="' + (ConvertTo-XmlText $file.RelativePath) + '" elementType="HTTPFileArg">')
        $null = $chunks.Add((Format-StringProp "File.path" $file.RelativePath))
        $null = $chunks.Add((Format-StringProp "File.paramname" $file.Field))
        $null = $chunks.Add((Format-StringProp "File.mimetype" $file.Mime))
        $null = $chunks.Add("</elementProp>")
    }
    $null = $chunks.Add("</collectionProp></elementProp>")
    return ($chunks -join "")
}
function Get-HttpLines([string]$Comment, [string]$Protocol, [string]$Domain, [string]$Port, [string]$Path, [string]$Method, $RawBody, $Arguments, $Files, [bool]$Multipart) {
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "TestPlan.comments" $Comment))
    if ($null -ne $RawBody) {
        $null = $lines.Add((Format-BoolProp "HTTPSampler.postBodyRaw" $true))
        $null = $lines.Add((Format-RawArguments ([string]$RawBody)))
    } else {
        $null = $lines.Add((Format-BoolProp "HTTPSampler.postBodyRaw" $false))
        $null = $lines.Add((Format-NamedArguments $Arguments))
    }
    $hasFiles = ($null -ne $Files -and $Files.Count -gt 0)
    if ($hasFiles) { $null = $lines.Add((Format-FileArguments $Files)) }
    $null = $lines.Add((Format-StringProp "HTTPSampler.domain" $Domain))
    $null = $lines.Add((Format-StringProp "HTTPSampler.port" $Port))
    $null = $lines.Add((Format-StringProp "HTTPSampler.protocol" $Protocol))
    $null = $lines.Add((Format-StringProp "HTTPSampler.contentEncoding" "UTF-8"))
    $null = $lines.Add((Format-StringProp "HTTPSampler.path" $Path))
    $null = $lines.Add((Format-StringProp "HTTPSampler.method" $Method))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.follow_redirects" $true))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.auto_redirects" $false))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.use_keepalive" $true))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.DO_MULTIPART_POST" ($Multipart -or $hasFiles)))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.BROWSER_COMPATIBLE_MULTIPART" $false))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.image_parser" $false))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.concurrentDwn" $false))
    $null = $lines.Add((Format-StringProp "HTTPSampler.concurrentPool" "6"))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.md5" $false))
    $null = $lines.Add((Format-BoolProp "HTTPSampler.monitor" $false))
    $null = $lines.Add((Format-StringProp "HTTPSampler.embedded_url_re" ""))
    $null = $lines.Add((Format-StringProp "HTTPSampler.connect_timeout" '${CONNECT_TIMEOUT_MS}'))
    $null = $lines.Add((Format-StringProp "HTTPSampler.response_timeout" '${RESPONSE_TIMEOUT_MS}'))
    $null = $lines.Add((Format-StringProp "HTTPSampler.implementation" "HttpClient4"))
    return ,$lines
}
function Add-HeaderManager([int]$Indent, [string]$Title, $Headers) {
    $lines = New-StringList
    $null = $lines.Add('<collectionProp name="HeaderManager.headers">')
    foreach ($header in $Headers) {
        $null = $lines.Add('<elementProp name="' + (ConvertTo-XmlText $header.Name) + '" elementType="Header">')
        $null = $lines.Add((Format-StringProp "Header.name" $header.Name))
        $null = $lines.Add((Format-StringProp "Header.value" $header.Value))
        $null = $lines.Add("</elementProp>")
    }
    $null = $lines.Add("</collectionProp>")
    Open-XmlBlock $Indent ('<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="' + (ConvertTo-XmlText $Title) + '" enabled="true">') $lines
    Close-XmlBlock $Indent
}
function Add-JsonExtractor([int]$Indent, [string]$Title, [string]$Name, [string]$Path, [string]$Default) {
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "JSONPostProcessor.referenceNames" $Name))
    $null = $lines.Add((Format-StringProp "JSONPostProcessor.jsonPathExprs" $Path))
    $null = $lines.Add((Format-StringProp "JSONPostProcessor.match_numbers" "1"))
    $null = $lines.Add((Format-StringProp "JSONPostProcessor.defaultValues" $Default))
    Open-XmlBlock $Indent ('<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="' + (ConvertTo-XmlText $Title) + '" enabled="true">') $lines
    Close-XmlBlock $Indent
}
function Add-ResponseAssertion([int]$Indent, [string]$Title, [string]$Expected) {
    $lines = New-StringList
    $null = $lines.Add('<collectionProp name="Asserion.test_strings">')
    $null = $lines.Add((Format-StringProp "0" $Expected))
    $null = $lines.Add("</collectionProp>")
    $null = $lines.Add((Format-StringProp "Assertion.custom_message" $Title))
    $null = $lines.Add((Format-StringProp "Assertion.test_field" "Assertion.response_code"))
    $null = $lines.Add((Format-BoolProp "Assertion.assume_success" $false))
    $null = $lines.Add('<intProp name="Assertion.test_type">8</intProp>')
    Open-XmlBlock $Indent ('<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="' + (ConvertTo-XmlText $Title) + '" enabled="true">') $lines
    Close-XmlBlock $Indent
}
function Add-Jsr223([int]$Indent, [string]$TestClass, [string]$Title, [string]$Script) {
    $safe = $Script.Replace("]]>", "]]]]><![CDATA[>")
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "scriptLanguage" "groovy"))
    $null = $lines.Add((Format-StringProp "parameters" ""))
    $null = $lines.Add((Format-StringProp "filename" ""))
    $null = $lines.Add((Format-StringProp "cacheKey" "true"))
    $null = $lines.Add('<stringProp name="script"><![CDATA[' + $safe + "]]></stringProp>")
    Open-XmlBlock $Indent ("<" + $TestClass + ' guiclass="TestBeanGUI" testclass="' + $TestClass + '" testname="' + (ConvertTo-XmlText $Title) + '" enabled="true">') $lines
    Close-XmlBlock $Indent
}
function Add-ModeIf([int]$Indent, [string]$Mode, [string]$Title) {
    $condition = '${__groovy("' + $Mode + '".equals(vars.get("COGNITO_TOKEN_MODE")))}'
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "IfController.condition" $condition))
    $null = $lines.Add((Format-BoolProp "IfController.evaluateAll" $false))
    $null = $lines.Add((Format-BoolProp "IfController.useExpression" $true))
    Open-XmlBlock $Indent ('<IfController guiclass="IfControllerPanel" testclass="IfController" testname="' + (ConvertTo-XmlText $Title) + '" enabled="true">') $lines
}
function Add-TokenChildren([int]$Indent, [string]$Script, $Headers, [string]$TokenPath, [string]$ExpiresPath) {
    Add-HeaderManager $Indent "Cognito request headers" $Headers
    Add-Jsr223 $Indent "JSR223PreProcessor" "Prepare Cognito token request" $Script
    Add-JsonExtractor $Indent "Extract access token" "ACCESS_TOKEN" $TokenPath "TOKEN_NOT_FOUND"
    Add-JsonExtractor $Indent "Extract expires_in" "EXPIRES_IN" $ExpiresPath "3600"
    Add-Jsr223 $Indent "JSR223PostProcessor" "Store access token for reuse" $script:StoreTokenScript
    Add-ResponseAssertion $Indent "Cognito token endpoint returned HTTP 200" "200"
}
function Add-TokenRequest([int]$Indent, [string]$Mode) {
    if ($Mode -eq "get_client_token") {
        $name = "POST GetClientToken"
        $protocol = '${COGNITO_GET_CLIENT_TOKEN_PROTOCOL}'
        $domain = '${COGNITO_RESOLVED_HOST}'
        $port = '${COGNITO_GET_CLIENT_TOKEN_PORT}'
        $path = "/"
        $body = '${COGNITO_TOKEN_BODY}'
        $scriptText = $script:PrepareGetClientTokenScript
        $headers = New-TextList
        Add-Text $headers "Content-Type" "application/x-amz-json-1.1"
        Add-Text $headers "X-Amz-Target" "AWSCognitoIdentityProviderService.GetClientToken"
        $tokenPath = '$.ClientAuthenticationResult.AccessToken'
        $expiresPath = '$.ClientAuthenticationResult.ExpiresIn'
        $comment = "ドメイン不要の GetClientToken。専用クライアントの ALLOW_CLIENT_TOKEN_AUTH だけで使う。"
    } else {
        $name = "POST /oauth2/token"
        $protocol = '${COGNITO_OAUTH_PROTOCOL}'
        $domain = '${COGNITO_DOMAIN}'
        $port = '${COGNITO_OAUTH_PORT}'
        $path = "/oauth2/token"
        $body = '${COGNITO_OAUTH_BODY}'
        $scriptText = $script:PrepareOauthScript
        $headers = New-TextList
        Add-Text $headers "Content-Type" $script:FormContent
        $tokenPath = '$.access_token'
        $expiresPath = '$.expires_in'
        $comment = "ユーザープールドメインの OAuth2 client_credentials。client_secret_post で送る。"
    }
    $lines = Get-HttpLines $comment $protocol $domain $port $path "POST" $body (New-TextList) (New-FileList) $false
    Open-XmlBlock $Indent ('<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="' + (ConvertTo-XmlText $name) + '" enabled="true">') $lines
    Add-TokenChildren ($Indent + 1) $scriptText $headers $tokenPath $expiresPath
    Close-XmlBlock $Indent
}
function Add-TokenFlow([int]$Indent, [bool]$IncludeValidation) {
    if ($IncludeValidation) { Add-Jsr223 $Indent "JSR223Sampler" "Validate Cognito settings" $script:ValidateScript }
    Add-ModeIf $Indent "get_client_token" "GetClientToken when selected"
    Add-TokenRequest ($Indent + 1) "get_client_token"
    Close-XmlBlock $Indent
    Add-ModeIf $Indent "oauth2_token" "OAuth token endpoint when selected"
    Add-TokenRequest ($Indent + 1) "oauth2_token"
    Close-XmlBlock $Indent
}
function Add-ApiSampler([int]$Indent, $Operation) {
    $accept = [string]$Operation.Accept
    if ([string]::IsNullOrEmpty($accept)) { $accept = $script:JsonContent }
    $headers = New-TextList
    Add-Text $headers "Authorization" 'Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}'
    Add-Text $headers "X-API-KEY" '${X_API_KEY}'
    Add-Text $headers "Accept" $accept
    if (-not [string]::IsNullOrEmpty([string]$Operation.ContentType) -and $Operation.ContentType -ne $script:MultipartContent) {
        Add-Text $headers "Content-Type" ([string]$Operation.ContentType)
    }
    foreach ($header in $Operation.Headers) {
        $lower = $header.Name.ToLowerInvariant()
        $seen = $false
        foreach ($existing in $headers) { if ($existing.Name.ToLowerInvariant() -eq $lower) { $seen = $true; break } }
        if (-not $seen) { Add-Text $headers $header.Name $header.Value }
    }
    if (-not [string]::IsNullOrEmpty([string]$Operation.CookieHeader)) { Add-Text $headers "Cookie" ([string]$Operation.CookieHeader) }
    $raw = $null
    $arguments = $Operation.Query
    $multipart = $false
    if ($Operation.ContentType -eq $script:MultipartContent) {
        $arguments = $Operation.MultipartFields
        $multipart = $true
        $raw = $null
    } elseif (-not [string]::IsNullOrEmpty([string]$Operation.ContentType)) { $raw = [string]$Operation.Body }
    $lines = Get-HttpLines ([string]$Operation.Summary) '${API_PROTOCOL}' '${API_HOST}' '${API_PORT}' ([string]$Operation.Path) ([string]$Operation.Method) $raw $arguments $Operation.Files $multipart
    Open-XmlBlock $Indent ('<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="' + (ConvertTo-XmlText $Operation.SamplerName) + '" enabled="true">') $lines
    Add-HeaderManager ($Indent + 1) "API Gateway headers" $headers
    Add-Jsr223 ($Indent + 1) "JSR223Assertion" "Access token is present" $script:TokenAssertionScript
    Close-XmlBlock $Indent
}
function Add-RefreshGate([int]$Indent) {
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "IfController.condition" '${TOKEN_NEEDS_REFRESH}'))
    $null = $lines.Add((Format-BoolProp "IfController.evaluateAll" $false))
    $null = $lines.Add((Format-BoolProp "IfController.useExpression" $true))
    Open-XmlBlock $Indent '<IfController guiclass="IfControllerPanel" testclass="IfController" testname="Refresh the access token when it is missing or near expiry" enabled="true">' $lines
    $lock = New-StringList
    $null = $lock.Add((Format-StringProp "CriticalSectionController.lockName" "cognito-access-token"))
    Open-XmlBlock ($Indent + 1) '<CriticalSectionController guiclass="CriticalSectionControllerGui" testclass="CriticalSectionController" testname="Cognito token refresh lock" enabled="true">' $lock
    Add-Jsr223 ($Indent + 2) "JSR223Sampler" "Recheck token expiry inside the lock" $script:RefreshCheckScript
    $inner = New-StringList
    $null = $inner.Add((Format-StringProp "IfController.condition" '${TOKEN_NEEDS_REFRESH}'))
    $null = $inner.Add((Format-BoolProp "IfController.evaluateAll" $false))
    $null = $inner.Add((Format-BoolProp "IfController.useExpression" $true))
    Open-XmlBlock ($Indent + 2) '<IfController guiclass="IfControllerPanel" testclass="IfController" testname="Only one thread requests a new token" enabled="true">' $inner
    Add-TokenFlow ($Indent + 3) $false
    Close-XmlBlock ($Indent + 2)
    Close-XmlBlock ($Indent + 1)
    Close-XmlBlock $Indent
}
function Get-ThreadLines([string]$Comment) {
    $lines = New-StringList
    $null = $lines.Add((Format-StringProp "TestPlan.comments" $Comment))
    $null = $lines.Add((Format-StringProp "ThreadGroup.on_sample_error" "continue"))
    $null = $lines.Add('<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">')
    $null = $lines.Add((Format-BoolProp "LoopController.continue_forever" $false))
    $null = $lines.Add((Format-StringProp "LoopController.loops" "1"))
    $null = $lines.Add("</elementProp>")
    $null = $lines.Add((Format-StringProp "ThreadGroup.num_threads" "1"))
    $null = $lines.Add((Format-StringProp "ThreadGroup.ramp_time" "1"))
    $null = $lines.Add((Format-BoolProp "ThreadGroup.scheduler" $false))
    $null = $lines.Add((Format-StringProp "ThreadGroup.duration" ""))
    $null = $lines.Add((Format-StringProp "ThreadGroup.delay" ""))
    $null = $lines.Add((Format-BoolProp "ThreadGroup.same_user_on_next_iteration" $true))
    return ,$lines
}
function Format-UserVariables($Options) {
    $pairs = @(
        @("API_PROTOCOL", [string]$Options.Protocol),
        @("API_HOST", [string]$Options.HostName),
        @("API_PORT", [string]$Options.Port),
        @("X_API_KEY", [string]$Options.ApiKey),
        @("CONNECT_TIMEOUT_MS", "10000"),
        @("RESPONSE_TIMEOUT_MS", "30000"),
        @("COGNITO_TOKEN_MODE", [string]$Options.TokenMode),
        @("COGNITO_REGION", "ap-northeast-1"),
        @("COGNITO_CLIENT_ID", "your-perf-client-id"),
        @("COGNITO_CLIENT_SECRET", "your-perf-client-secret"),
        @("COGNITO_SCOPES", ""),
        @("COGNITO_DOMAIN", "your-domain.auth.ap-northeast-1.amazoncognito.com"),
        @("COGNITO_GET_CLIENT_TOKEN_PROTOCOL", "https"),
        @("COGNITO_GET_CLIENT_TOKEN_HOST", ""),
        @("COGNITO_GET_CLIENT_TOKEN_PORT", "443"),
        @("COGNITO_OAUTH_PROTOCOL", "https"),
        @("COGNITO_OAUTH_PORT", "443"),
        @("TOKEN_REFRESH_SKEW_SECONDS", "60")
    )
    $chunks = New-StringList
    $null = $chunks.Add('<elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true"><collectionProp name="Arguments.arguments">')
    foreach ($pair in $pairs) {
        $value = '${__P(' + $pair[0] + ',' + $pair[1] + ')}'
        $null = $chunks.Add('<elementProp name="' + (ConvertTo-XmlText $pair[0]) + '" elementType="Argument">')
        $null = $chunks.Add((Format-StringProp "Argument.name" $pair[0]))
        $null = $chunks.Add((Format-StringProp "Argument.value" $value))
        $null = $chunks.Add((Format-StringProp "Argument.metadata" "="))
        $null = $chunks.Add("</elementProp>")
    }
    $null = $chunks.Add("</collectionProp></elementProp>")
    return ($chunks -join "")
}
function Format-SaveConfig {
    $fields = @(
        @("time", "true"), @("latency", "true"), @("timestamp", "true"), @("success", "true"), @("label", "true"),
        @("code", "true"), @("message", "true"), @("threadName", "true"), @("dataType", "true"), @("encoding", "true"),
        @("assertions", "true"), @("subresults", "true"), @("responseData", "true"), @("samplerData", "true"), @("xml", "true"),
        @("fieldNames", "true"), @("responseHeaders", "true"), @("requestHeaders", "true"), @("responseDataOnError", "false"),
        @("saveAssertionResultsFailureMessage", "true"), @("bytes", "true"), @("sentBytes", "true"), @("url", "true"),
        @("threadCounts", "true"), @("idleTime", "true"), @("connectTime", "true"), @("sampleCount", "true"),
        @("hostname", "true"), @("fileName", "false")
    )
    $sb = New-Object System.Text.StringBuilder
    $null = $sb.Append('<objProp><name>saveConfig</name><value class="SampleSaveConfiguration">')
    foreach ($field in $fields) { $null = $sb.Append("<" + $field[0] + ">" + $field[1] + "</" + $field[0] + ">") }
    $null = $sb.Append("<assertionsResultsToSave>2</assertionsResultsToSave></value></objProp>")
    return $sb.ToString()
}
function Add-PlanChildren($Operations) {
    $defaults = New-StringList
    $null = $defaults.Add((Format-StringProp "TestPlan.comments" "タイムアウトを必ず持たせ、応答がない相手を待ち続けない。ホストは各リクエストで明示する。"))
    $null = $defaults.Add('<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true"><collectionProp name="Arguments.arguments"/></elementProp>')
    $null = $defaults.Add((Format-StringProp "HTTPSampler.implementation" "HttpClient4"))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.connect_timeout" '${CONNECT_TIMEOUT_MS}'))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.response_timeout" '${RESPONSE_TIMEOUT_MS}'))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.protocol" ""))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.domain" ""))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.port" ""))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.contentEncoding" "UTF-8"))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.path" ""))
    $null = $defaults.Add((Format-StringProp "HTTPSampler.concurrentPool" "6"))
    Open-XmlBlock 3 '<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">' $defaults
    Close-XmlBlock 3
    $cookies = New-StringList
    $null = $cookies.Add('<collectionProp name="CookieManager.cookies"/>')
    $null = $cookies.Add((Format-BoolProp "CookieManager.clearEachIteration" $false))
    $null = $cookies.Add((Format-BoolProp "CookieManager.controlledByThreadGroup" $false))
    $null = $cookies.Add((Format-StringProp "CookieManager.policy" "standard"))
    $null = $cookies.Add((Format-StringProp "CookieManager.implementation" "org.apache.jmeter.protocol.http.control.HC4CookieHandler"))
    Open-XmlBlock 3 '<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="HTTP Cookie Manager" enabled="true">' $cookies
    Close-XmlBlock 3
    $cache = New-StringList
    $null = $cache.Add((Format-StringProp "TestPlan.comments" "API確認ではキャッシュが実リクエストを隠すため無効。ブラウザ模擬のときだけ有効にする。"))
    $null = $cache.Add((Format-BoolProp "clearEachIteration" $true))
    $null = $cache.Add((Format-BoolProp "useExpires" $false))
    Open-XmlBlock 3 '<CacheManager guiclass="CacheManagerGui" testclass="CacheManager" testname="HTTP Cache Manager" enabled="false">' $cache
    Close-XmlBlock 3
    $dns = New-StringList
    $null = $dns.Add('<collectionProp name="DNSCacheManager.servers"/>')
    $null = $dns.Add((Format-BoolProp "DNSCacheManager.clearEachIteration" $false))
    $null = $dns.Add((Format-BoolProp "DNSCacheManager.isCustomResolver" $false))
    Open-XmlBlock 3 '<DNSCacheManager guiclass="DNSCachePanel" testclass="DNSCacheManager" testname="DNS Cache Manager" enabled="true">' $dns
    Close-XmlBlock 3
    Open-XmlBlock 3 '<SetupThreadGroup guiclass="SetupThreadGroupGui" testclass="SetupThreadGroup" testname="setUp - Cognito access token" enabled="true">' (Get-ThreadLines "本スレッドの前に1回だけ動き、アクセストークンをプロパティへ保存する。")
    Add-TokenFlow 4 $true
    Close-XmlBlock 3
    Open-XmlBlock 3 '<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="API calls - 1 thread 1 iteration" enabled="true">' (Get-ThreadLines "定義済みAPIを1スレッド、1反復で順番に1回ずつ呼ぶ。")
    Add-Jsr223 4 "JSR223Sampler" "Decide whether the access token needs refreshing" $script:RefreshCheckScript
    Add-RefreshGate 4
    $grouped = New-Object "System.Collections.Generic.Dictionary[string,object]"
    foreach ($operation in $Operations) {
        if (-not $grouped.ContainsKey([string]$operation.Tag)) { $grouped[[string]$operation.Tag] = (New-Object System.Collections.Generic.List[object]) }
        $null = $grouped[[string]$operation.Tag].Add($operation)
    }
    $tags = New-Object System.Collections.Generic.List[string]
    foreach ($key in $grouped.Keys) { $null = $tags.Add($key) }
    $tags.Sort([System.StringComparer]::Ordinal)
    foreach ($tag in $tags) {
        $tx = New-StringList
        $null = $tx.Add((Format-BoolProp "TransactionController.includeTimers" $false))
        $null = $tx.Add((Format-BoolProp "TransactionController.parent" $true))
        Open-XmlBlock 4 ('<TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" testname="' + (ConvertTo-XmlText $tag) + '" enabled="true">') $tx
        foreach ($operation in $grouped[$tag]) { Add-ApiSampler 5 $operation }
        Close-XmlBlock 4
    }
    Close-XmlBlock 3
    $listener = New-StringList
    $null = $listener.Add((Format-StringProp "TestPlan.comments" "XML形式のjtlなので、GUIのView Results Treeでリクエストとレスポンスを開ける。大規模な負荷試験の前にはこのリスナーを無効にする。"))
    $null = $listener.Add((Format-BoolProp "ResultCollector.error_logging" $false))
    $null = $listener.Add((Format-SaveConfig))
    $null = $listener.Add((Format-StringProp "filename" "results/jmeter-results.jtl"))
    Open-XmlBlock 3 '<ResultCollector guiclass="ViewResultsFullVisualizer" testclass="ResultCollector" testname="View Results Tree" enabled="true">' $listener
    Close-XmlBlock 3
}
function Build-JmxText($Index, $Operations, $Options, $Seed) {
    $created = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [System.Globalization.CultureInfo]::InvariantCulture)
    $title = "OpenAPI API test"
    if ($Index.Root -is [YamlMap]) {
        $info = $Index.Root.Get("info")
        if ($info -is [YamlMap] -and -not [string]::IsNullOrEmpty([string]$info.Get("title"))) { $title = [string]$info.Get("title") }
    }
    $comment = @(
        ($title + " を1回ずつ呼ぶ確認用シナリオです。"),
        "最初に setUp スレッドが Cognito からアクセストークンを1回受け取り、JMeterプロパティ ACCESS_TOKEN に保存します。",
        "後続のAPIは Authorization: Bearer とそのトークン、および X-API-KEY を付けて http://localhost:8080 へ送ります。",
        "トークンの期限が来る少し前だけ、同じ取得処理をやり直します。",
        ("生成時刻: " + $created),
        ("乱数シード: " + $Seed),
        ("トークン方式の初期値: " + $Options.TokenMode),
        "実行前に COGNITO_CLIENT_ID と COGNITO_CLIENT_SECRET を性能試験専用アプリクライアントの値へ変えてください。",
        "callbacks と webhooks はシナリオに含めていません。",
        "結果の jtl にはトークン応答が含まれるため、結果ファイルは秘密情報として扱ってください。jmeter.log にはトークン本文を出しません。",
        "GUIで結果を見るときは、View Results Tree のファイル名 results/jmeter-results.jtl を開き直してください。"
    ) -join "`n"
    Reset-Xml
    Add-XmlLine 0 '<?xml version="1.0" encoding="UTF-8"?>'
    Add-XmlLine 0 '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">'
    Add-XmlLine 1 "<hashTree>"
    $plan = New-StringList
    $null = $plan.Add((Format-StringProp "TestPlan.comments" $comment))
    $null = $plan.Add((Format-BoolProp "TestPlan.functional_mode" $false))
    $null = $plan.Add((Format-BoolProp "TestPlan.tearDown_on_shutdown" $true))
    $null = $plan.Add((Format-BoolProp "TestPlan.serialize_threadgroups" $false))
    $null = $plan.Add((Format-UserVariables $Options))
    $null = $plan.Add((Format-StringProp "TestPlan.user_define_classpath" ""))
    Open-XmlBlock 2 ('<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="' + (ConvertTo-XmlText ($title + " smoke")) + '" enabled="true">') $plan
    Add-PlanChildren $Operations
    Close-XmlBlock 2
    Add-XmlLine 1 "</hashTree>"
    Add-XmlLine 0 "</jmeterTestPlan>"
    return (Complete-Xml)
}
function Write-PlanFiles([string]$Output, [string]$Jmx, $Files) {
    $full = [System.IO.Path]::GetFullPath($Output)
    $parent = [System.IO.Path]::GetDirectoryName($full)
    if (-not [string]::IsNullOrEmpty($parent)) { [void][System.IO.Directory]::CreateDirectory($parent) }
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($full, ($Jmx.Replace("`r`n", "`n").Replace("`r", "`n")), $encoding)
    if ($null -eq $Files) { return }
    foreach ($file in $Files) {
        $dest = Join-Path $parent ($file.RelativePath.Replace("/", [System.IO.Path]::DirectorySeparatorChar))
        $destDir = [System.IO.Path]::GetDirectoryName($dest)
        if (-not [string]::IsNullOrEmpty($destDir)) { [void][System.IO.Directory]::CreateDirectory($destDir) }
        [System.IO.File]::WriteAllBytes($dest, $file.Bytes)
    }
}
function Write-Stdout([string]$Message) { [Console]::Out.WriteLine($Message) }
function Write-Stderr([string]$Message) { [Console]::Error.WriteLine($Message) }
function Find-SpecException($ErrorRecord) {
    $pending = New-Object System.Collections.Generic.Queue[object]
    if ($null -ne $ErrorRecord) { $pending.Enqueue($ErrorRecord) }
    $guard = 0
    while ($pending.Count -gt 0 -and $guard -lt 30) {
        $guard++
        $current = $pending.Dequeue()
        if ($null -eq $current) { continue }
        if ($current -is [SpecException]) { return $current }
        if ($current -is [System.Management.Automation.ErrorRecord]) { $pending.Enqueue($current.Exception); continue }
        $inner = $null
        try { $inner = $current.InnerException } catch { $inner = $null }
        if ($null -ne $inner -and -not [object]::ReferenceEquals($inner, $current)) { $pending.Enqueue($inner) }
    }
    return $null
}
function Show-JmxHelp {
    Write-Stdout "OpenAPI定義から、Cognitoアクセストークン付きの JMeter 5.6.3 シナリオを生成します。"
    Write-Stdout "powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate-jmx.ps1 -i 定義.yaml -o シナリオ.jmx"
    Write-Stdout "-i, --openapi       OpenAPI 3 または Swagger 2 のYAML/JSON"
    Write-Stdout "-o, --output        出力する .jmx ファイル"
    Write-Stdout "--host              APIの初期ホスト。既定は localhost"
    Write-Stdout "--port              APIの初期ポート。既定は 8080"
    Write-Stdout "--protocol          http または https。既定は http"
    Write-Stdout "--api-key           X-API-KEY の初期値。既定は XXXXXXXXXXXX"
    Write-Stdout "--base-path         auto、none、または /v1 のようなパス"
    Write-Stdout "--server-index      base path に使う servers の番号。既定は 0"
    Write-Stdout "--seed              テストデータを再現する乱数シード"
    Write-Stdout "--required-only     必須の項目だけを送る"
    Write-Stdout "--skip-deprecated   deprecated な operation を含めない"
    Write-Stdout "--methods           含めるメソッド。例: get,post"
    Write-Stdout "--token-mode        get_client_token または oauth2_token。既定は get_client_token"
}
function Read-OptionValue($Items, $Index, $Inline, $Name) {
    if ($null -ne $Inline) { return [string]$Inline }
    $next = [int]$Index + 1
    if ($next -ge $Items.Count) { Fail ([string]$Name + " の値がありません。") 2 }
    return [string]$Items[$next]
}
function ConvertTo-JmxOptions($Items) {
    $opt = New-Object PSObject -Property @{
        OpenApi = ""; Output = ""; HostName = "localhost"; Port = "8080"; Protocol = "http"; ApiKey = "XXXXXXXXXXXX"
        BasePath = "auto"; ServerIndex = 0; Seed = $null; RequiredOnly = $false; SkipDeprecated = $false
        Methods = ""; TokenMode = "get_client_token"; ShowHelp = $false
    }
    for ($index = 0; $index -lt $Items.Count; $index++) {
        $token = [string]$Items[$index]
        if ($token -eq "-h" -or $token -eq "--help" -or $token -eq "-help") { $opt.ShowHelp = $true; continue }
        $name = $token
        $inline = $null
        $eq = $token.IndexOf("=")
        if ($token.StartsWith("--") -and $eq -gt 2) { $name = $token.Substring(0, $eq); $inline = $token.Substring($eq + 1) }
        $usedNext = $false
        if ($name -eq "-i" -or $name -eq "--openapi") { $opt.OpenApi = (Read-OptionValue $Items $index $inline "-i"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "-o" -or $name -eq "--output") { $opt.Output = (Read-OptionValue $Items $index $inline "-o"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--host") { $opt.HostName = (Read-OptionValue $Items $index $inline "--host"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--port") { $opt.Port = (Read-OptionValue $Items $index $inline "--port"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--protocol") { $opt.Protocol = (Read-OptionValue $Items $index $inline "--protocol"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--api-key") { $opt.ApiKey = (Read-OptionValue $Items $index $inline "--api-key"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--base-path") { $opt.BasePath = (Read-OptionValue $Items $index $inline "--base-path"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--server-index") {
            $text = Read-OptionValue $Items $index $inline "--server-index"
            $parsed = 0
            if (-not [int]::TryParse($text, [ref]$parsed)) { Fail "server-index は整数です。" 2 }
            $opt.ServerIndex = $parsed
            $usedNext = ($null -eq $inline)
        } elseif ($name -eq "--seed") {
            $text = Read-OptionValue $Items $index $inline "--seed"
            $parsed = 0
            if (-not [int]::TryParse($text, [ref]$parsed)) { Fail "seed は整数です。" 2 }
            $opt.Seed = $parsed
            $usedNext = ($null -eq $inline)
        } elseif ($name -eq "--methods") { $opt.Methods = (Read-OptionValue $Items $index $inline "--methods"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--token-mode") { $opt.TokenMode = (Read-OptionValue $Items $index $inline "--token-mode"); $usedNext = ($null -eq $inline) }
        elseif ($name -eq "--required-only") { $opt.RequiredOnly = $true }
        elseif ($name -eq "--skip-deprecated") { $opt.SkipDeprecated = $true }
        else { Fail ("未対応の引数です: " + $token) 2 }
        if ($usedNext) { $index++ }
    }
    if ($opt.Protocol -ne "http" -and $opt.Protocol -ne "https") { Fail "protocol は http または https です。" 2 }
    if ($opt.TokenMode -ne "get_client_token" -and $opt.TokenMode -ne "oauth2_token") { Fail "token-mode は get_client_token または oauth2_token です。" 2 }
    return $opt
}
function Invoke-JmxMain {
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
    try {
        $opt = ConvertTo-JmxOptions @($args)
        if ($opt.ShowHelp) { Show-JmxHelp; return 0 }
        if ([string]::IsNullOrEmpty([string]$opt.OpenApi) -or [string]::IsNullOrEmpty([string]$opt.Output)) { Fail "-i と -o は必須です。" 2 }
        if (-not (Test-Path -LiteralPath $opt.OpenApi -PathType Leaf)) {
            Write-Stderr ("OpenAPIファイルがありません: " + $opt.OpenApi)
            return 3
        }
        $seed = $opt.Seed
        if ($null -eq $seed) { $seed = Get-Random -Minimum 1 -Maximum 2147483647 }
        $rng = New-Object System.Random ([int]$seed)
        $document = Import-SpecFile ([string]$opt.OpenApi)
        $index = New-Index ([string]$opt.OpenApi) $document
        $built = Build-Operations $index $opt $rng
        $jmx = Build-JmxText $index $built.Operations $opt ([int]$seed)
        Write-PlanFiles ([string]$opt.Output) $jmx $built.Files
        Write-Stdout ("JMeterシナリオを書きました: " + $opt.Output)
        Write-Stdout ("API数: " + $built.Operations.Count)
        Write-Stdout ("呼び出し先の初期値: " + $opt.Protocol + "://" + $opt.HostName + ":" + $opt.Port)
        Write-Stdout ("X-API-KEYの初期値: " + $opt.ApiKey)
        Write-Stdout ("トークン方式の初期値: " + $opt.TokenMode)
        Write-Stdout ("テストデータのシード: " + $seed)
        Write-Stdout "結果ファイル: results/jmeter-results.jtl （テスト計画のあるディレクトリ基準）"
        Write-Stdout "ログファイル: 実行スクリプトが results/jmeter.log を -j で指定します。"
        return 0
    } catch {
        $spec = Find-SpecException $_
        if ($null -ne $spec) { Write-Stderr ([string]$spec.Message); return [int]$spec.ExitCode }
        $message = $_.Exception.Message
        if ([string]::IsNullOrEmpty($message)) { $message = [string]$_ }
        Write-Stderr $message
        return 3
    }
}
