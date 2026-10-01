# OpenAPI 3 / Swagger 2 から JMeter 5.6.3 の確認用シナリオを作ります。
# Windows PowerShell 5.1 だけで動きます。Python は使いません。
# 使い方:
#   powershell -NoProfile -File .\scripts\generate-jmx.ps1 -i examples\petstore.openapi.yaml -o examples\petstore.jmx
$ErrorActionPreference = "Stop"

if (-not ("YamlMap" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
public class YamlPair {
    public string Key;
    public object Value;
    public YamlPair(string key, object value) { Key = key; Value = value; }
}
public class YamlMap : List<YamlPair> {
    public bool Has(string key) {
        foreach (YamlPair item in this) if (item.Key == key) return true;
        return false;
    }
    public object Get(string key) {
        foreach (YamlPair item in this) if (item.Key == key) return item.Value;
        return null;
    }
    public void Set(string key, object value) {
        for (int i = 0; i < Count; i++) {
            if (this[i].Key == key) { this[i] = new YamlPair(key, value); return; }
        }
        Add(new YamlPair(key, value));
    }
}
public class YamlSeq : List<object> {}
public class TextPair {
    public string Name;
    public string Value;
    public TextPair(string name, string value) { Name = name; Value = value; }
}
public class TextList : List<TextPair> {}
public class FileArg {
    public string RelativePath;
    public string Field;
    public string Mime;
    public FileArg(string relativePath, string field, string mime) {
        RelativePath = relativePath; Field = field; Mime = mime;
    }
}
public class FileList : List<FileArg> {}
public class ByteFile {
    public string RelativePath;
    public byte[] Bytes;
    public ByteFile(string relativePath, byte[] bytes) { RelativePath = relativePath; Bytes = bytes; }
}
public class ByteFileList : List<ByteFile> {}
"@
}

$script:HttpMethods = @("get", "post", "put", "patch", "delete", "head", "options", "trace")
$script:JsonContent = "application/json"
$script:FormContent = "application/x-www-form-urlencoded"
$script:MultipartContent = "multipart/form-data"
$script:TextContent = "text/plain"
$script:XmlContent = "application/xml"
$script:BinaryContent = "application/octet-stream"
$script:ContentPreference = @($script:JsonContent, $script:FormContent, $script:MultipartContent, $script:TextContent, $script:XmlContent, $script:BinaryContent)
$script:Alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
$script:Letters = "abcdefghijklmnopqrstuvwxyz"
$script:KeyValue = New-Object System.Text.RegularExpressions.Regex '^(?<key>(?:"(?:\\.|[^"])*"|''(?:''''|[^''])*''|[^:#''"\[]+?)):(?<rest>.*)$', ([System.Text.RegularExpressions.RegexOptions]::Compiled)

function As-One($Value) { ,$Value }

function New-Map { $map = New-Object YamlMap; ,$map }
function New-Seq { $seq = New-Object YamlSeq; ,$seq }
function New-TextList { $list = New-Object TextList; ,$list }
function New-FileList { $list = New-Object FileList; ,$list }
function New-ByteFileList { $list = New-Object ByteFileList; ,$list }

function Test-Map($Value) { return $Value -is [YamlMap] }
function Test-Seq($Value) { return $Value -is [YamlSeq] }
function Test-Number($Value) {
    if ($Value -is [bool]) { return $false }
    return ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal] -or $Value -is [float] -or $Value -is [single])
}

class SpecException : System.Exception {
    [int] $ExitCode
    SpecException([string] $Message, [int] $Code) : base($Message) { $this.ExitCode = $Code }
}
function Fail([string]$Message, [int]$Code) { throw [SpecException]::new($Message, $Code) }

function Copy-Node($Value) {
    if (Test-Map $Value) {
        $copy = New-Map
        foreach ($pair in $Value) { $copy.Set($pair.Key, (Copy-Node $pair.Value)) }
        return ,$copy
    }
    if (Test-Seq $Value) {
        $copy = New-Seq
        foreach ($item in $Value) { $null = $copy.Add((Copy-Node $item)) }
        return ,$copy
    }
    return $Value
}

function ConvertTo-Scalar([string]$Token) {
    $Token = $Token.Trim()
    if ($Token -eq "" -or $Token -eq "null" -or $Token -eq "Null" -or $Token -eq "NULL" -or $Token -eq "~") { return $null }
    if ($Token -eq "true" -or $Token -eq "True" -or $Token -eq "TRUE") { return $true }
    if ($Token -eq "false" -or $Token -eq "False" -or $Token -eq "FALSE") { return $false }
    if ($Token.Length -ge 2 -and $Token[0] -eq $Token[$Token.Length - 1] -and ($Token[0] -eq "'" -or $Token[0] -eq '"')) {
        return (ConvertFrom-Quoted $Token)
    }
    if ($Token -match '^[+-]?\d+$') {
        $longValue = 0L
        if ([long]::TryParse($Token, [ref]$longValue)) {
            if ($longValue -le [int]::MaxValue -and $longValue -ge [int]::MinValue) { return [int]$longValue }
            return $longValue
        }
    }
    $doubleValue = 0.0
    if ($Token -match '^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$|^\d+[eE][+-]?\d+$') {
        if ([double]::TryParse($Token, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$doubleValue)) {
            return $doubleValue
        }
    }
    return $Token
}

function ConvertFrom-Quoted([string]$Token) {
    $quote = $Token[0]
    $body = $Token.Substring(1, $Token.Length - 2)
    if ($quote -eq "'") { return $body.Replace("''", "'") }
    $result = New-Object System.Text.StringBuilder
    for ($index = 0; $index -lt $body.Length; $index++) {
        $char = $body[$index]
        if ($char -ne "\") { $null = $result.Append($char); continue }
        if ($index + 1 -ge $body.Length) { Fail "引用符の中のエスケープが閉じていません" 3 }
        $escaped = $body[$index + 1]
        $mapping = @{ "n" = "`n"; "t" = "`t"; "r" = "`r"; "\" = "\"; '"' = '"'; "/" = "/" }
        if ($mapping.ContainsKey([string]$escaped)) { $null = $result.Append($mapping[[string]$escaped]); $index++; continue }
        if ($escaped -eq "u" -and $index + 5 -lt $body.Length) {
            $null = $result.Append([char][Convert]::ToInt32($body.Substring($index + 2, 4), 16))
            $index += 5
            continue
        }
        Fail "未対応のエスケープです: \$escaped" 3
    }
    return $result.ToString()
}

function Remove-YamlComment([string]$Line) {
    $single = $false
    $double = $false
    $escaped = $false
    for ($index = 0; $index -lt $Line.Length; $index++) {
        $char = $Line[$index]
        if ($double) {
            if ($escaped) { $escaped = $false }
            elseif ($char -eq "\") { $escaped = $true }
            elseif ($char -eq '"') { $double = $false }
            continue
        }
        if ($single) {
            if ($char -eq "'" -and $index + 1 -lt $Line.Length -and $Line[$index + 1] -eq "'") { $index++; continue }
            if ($char -eq "'") { $single = $false }
            continue
        }
        if ($char -eq "#") { return $Line.Substring(0, $index) }
        if ($char -eq '"') { $double = $true }
        elseif ($char -eq "'") { $single = $true }
    }
    return $Line
}

function Get-Indent([string]$Raw) { return $Raw.Length - $Raw.TrimStart(" ").Length }
function Test-DocumentMarker([string]$Raw) {
    if ($Raw.StartsWith(" ") -or $Raw.StartsWith("`t")) { return $false }
    $stripped = (Remove-YamlComment $Raw).Trim()
    return ($stripped -eq "---" -or $stripped -eq "...")
}

function New-Parser([string]$Text) {
    $lines = New-Object System.Collections.Generic.List[object]
    $number = 0
    foreach ($raw in ($Text -replace "`r`n", "`n" -replace "`r", "`n").Split("`n")) {
        $number++
        $indentPart = $raw.Substring(0, $raw.Length - $raw.TrimStart(" `t").Length)
        if ($indentPart.Contains("`t")) { Fail "YAML line ${number}: tabs cannot be used for indentation" 3 }
        $null = $lines.Add((New-Object PSObject -Property @{ Number = $number; Text = $raw.TrimEnd("`r") }))
    }
    $anchors = New-Map
    return ,@{ Lines = $lines; Index = 0; Anchors = $anchors }
}

function Update-ParserPeek($Parser) {
    while ($Parser.Index -lt $Parser.Lines.Count) {
        $raw = [string]$Parser.Lines[$Parser.Index].Text
        $stripped = (Remove-YamlComment $raw).Trim()
        if ($stripped -eq "") { $Parser.Index++; continue }
        if (Test-DocumentMarker $raw) { return $null }
        return $Parser.Lines[$Parser.Index]
    }
    return $null
}
function Move-ParserPreamble($Parser) {
    while ($Parser.Index -lt $Parser.Lines.Count) {
        $raw = [string]$Parser.Lines[$Parser.Index].Text
        $stripped = (Remove-YamlComment $raw).Trim()
        if ($stripped -eq "" -or $stripped.StartsWith("%") -or (Test-DocumentMarker $raw)) { $Parser.Index++; continue }
        return
    }
}
function Complete-ParserDocument($Parser) {
    while ($Parser.Index -lt $Parser.Lines.Count) {
        $item = $Parser.Lines[$Parser.Index]
        $stripped = (Remove-YamlComment ([string]$item.Text)).Trim()
        if ($stripped -eq "") { $Parser.Index++; continue }
        if (Test-DocumentMarker ([string]$item.Text)) { $Parser.Index++; break }
        Fail "YAML line $($item.Number): only one YAML document is supported" 3
    }
    while ($Parser.Index -lt $Parser.Lines.Count) {
        $item = $Parser.Lines[$Parser.Index]
        $stripped = (Remove-YamlComment ([string]$item.Text)).Trim()
        if ($stripped -eq "" -or $stripped.StartsWith("%")) { $Parser.Index++; continue }
        Fail "YAML line $($item.Number): only one YAML document is supported" 3
    }
}
function Pop-ParserLine($Parser) {
    $item = Update-ParserPeek $Parser
    if ($null -eq $item) { Fail "YAML が途中で終わっています" 3 }
    $Parser.Index++
    return $item
}

function Import-YamlText([string]$Text) {
    if ($Text.Length -gt 0 -and [int]$Text[0] -eq 0xFEFF) { $Text = $Text.Substring(1) }
    $parser = New-Parser $Text
    Move-ParserPreamble $parser
    $document = Read-YamlBlock $parser 0
    Complete-ParserDocument $parser
    return ,$document
}

function Read-YamlBlock($Parser, [int]$MinimumIndent) {
    $peeked = Update-ParserPeek $Parser
    if ($null -eq $peeked) { return $null }
    $indent = Get-Indent ([string]$peeked.Text)
    if ($indent -lt $MinimumIndent) { return $null }
    $content = (Remove-YamlComment ([string]$peeked.Text)).Trim()
    if ($content.StartsWith("-")) { return ,(Read-YamlSequence $Parser $indent) }
    return ,(Read-YamlMapping $Parser $indent)
}
function Read-YamlSequence($Parser, [int]$Indent) {
    $items = New-Seq
    while ($true) {
        $peeked = Update-ParserPeek $Parser
        if ($null -eq $peeked) { break }
        if ((Get-Indent ([string]$peeked.Text)) -ne $Indent) { break }
        $content = Remove-YamlComment ([string]$peeked.Text)
        if (-not $content.Trim().StartsWith("-")) { break }
        $line = Pop-ParserLine $Parser
        $split = Split-SequenceBody ([string]$line.Text) ([int]$line.Number)
        $anchorSplit = Split-Anchor $split.Body
        $value = Read-SequenceValue $Parser ([int]$line.Number) $split.KeyIndent $Indent $anchorSplit.Rest
        Save-Anchor $Parser $anchorSplit.Name $value
        $null = $items.Add($value)
    }
    return ,$items
}
function Split-SequenceBody([string]$Raw, [int]$LineNo) {
    $content = Remove-YamlComment $Raw
    $leading = Get-Indent $content
    $without = $content.Substring($leading)
    if (-not $without.StartsWith("-")) { Fail "YAML line ${LineNo}: expected a sequence dash" 3 }
    $after = $without.Substring(1)
    if ($after -ne "" -and -not $after.StartsWith(" ")) { Fail "YAML line ${LineNo}: expected a space after the sequence dash" 3 }
    $spaces = $after.Length - $after.TrimStart(" ").Length
    return @{ Body = $after.Substring($spaces); KeyIndent = ($leading + 1 + $spaces) }
}
function Read-SequenceValue($Parser, [int]$LineNo, [int]$KeyIndent, [int]$DashIndent, [string]$Body) {
    $Body = $Body.Trim()
    if ($Body -eq "") { return ,(Read-YamlNested $Parser $DashIndent) }
    if ($Body.StartsWith("*") -and $Body -match '^\*[A-Za-z0-9_-]+$') { return ,(Copy-Node ($Parser.Anchors.Get($Body.Substring(1)))) }
    if ($Body.StartsWith("{") -or $Body.StartsWith("[")) {
        $flow = Collect-Flow $Parser $Body $LineNo
        return ,(Read-Flow $flow $LineNo $Parser.Anchors)
    }
    if ($Body -match '^[|>][+-]?\d*$') { return (Read-BlockScalar $Parser $Body $DashIndent) }
    if ($script:KeyValue.IsMatch($Body)) { return ,(Read-MappingFromFirst $Parser $LineNo $KeyIndent $Body) }
    return (ConvertTo-Scalar $Body)
}
function Read-MappingFromFirst($Parser, [int]$LineNo, [int]$KeyIndent, [string]$FirstBody) {
    $mapping = New-Map
    Add-MappingPair $Parser $mapping $LineNo $KeyIndent $FirstBody
    while ($true) {
        $peeked = Update-ParserPeek $Parser
        if ($null -eq $peeked) { break }
        if ((Get-Indent ([string]$peeked.Text)) -ne $KeyIndent) { break }
        $stripped = (Remove-YamlComment ([string]$peeked.Text)).Trim()
        if ($stripped.StartsWith("-") -or -not $script:KeyValue.IsMatch($stripped)) { break }
        $line = Pop-ParserLine $Parser
        Add-MappingPair $Parser $mapping ([int]$line.Number) $KeyIndent $stripped
    }
    return ,$mapping
}
function Add-MappingPair($Parser, $Mapping, [int]$LineNo, [int]$KeyIndent, [string]$Body) {
    $match = $script:KeyValue.Match($Body.Trim())
    if (-not $match.Success) { Fail "YAML line ${LineNo}: expected a key" 3 }
    $key = [string](ConvertTo-Scalar $match.Groups["key"].Value.Trim())
    $rest = $match.Groups["rest"].Value
    if ($rest.StartsWith(" ")) { $rest = $rest.Substring(1) }
    elseif ($rest -ne "") { Fail "YAML line ${LineNo}: expected a space after the mapping colon" 3 }
    $anchorSplit = Split-Anchor $rest
    $value = Read-ValueAfter $Parser $LineNo "" $KeyIndent $anchorSplit.Rest ($KeyIndent + 2)
    Save-Anchor $Parser $anchorSplit.Name $value
    if ($key -eq "<<") { Merge-Map $Mapping $value $LineNo } else { $Mapping.Set($key, $value) }
}
function Read-YamlMapping($Parser, [int]$Indent) {
    $mapping = New-Map
    while ($true) {
        $peeked = Update-ParserPeek $Parser
        if ($null -eq $peeked) { break }
        if ((Get-Indent ([string]$peeked.Text)) -ne $Indent) { break }
        $stripped = (Remove-YamlComment ([string]$peeked.Text)).Trim()
        if ($stripped.StartsWith("-")) { break }
        $match = $script:KeyValue.Match($stripped)
        if (-not $match.Success) { Fail "YAML line $($peeked.Number): expected a key, found $stripped" 3 }
        $line = Pop-ParserLine $Parser
        $key = [string](ConvertTo-Scalar $match.Groups["key"].Value.Trim())
        $rest = $match.Groups["rest"].Value
        if ($rest.StartsWith(" ")) { $rest = $rest.Substring(1) }
        elseif ($rest -ne "") { Fail "YAML line $($line.Number): expected a space after the mapping colon" 3 }
        $anchorSplit = Split-Anchor $rest
        $value = Read-ValueAfter $Parser ([int]$line.Number) ([string]$line.Text) $Indent $anchorSplit.Rest ($Indent + 2)
        Save-Anchor $Parser $anchorSplit.Name $value
        if ($key -eq "<<") { Merge-Map $mapping $value ([int]$line.Number) } else { $mapping.Set($key, $value) }
    }
    return ,$mapping
}
function Read-ValueAfter($Parser, [int]$LineNo, [string]$RawLine, [int]$ParentIndent, [string]$Rest, [int]$ItemIndent) {
    $Rest = $Rest.Trim()
    if ($Rest.StartsWith("*") -and $Rest -match '^\*[A-Za-z0-9_-]+$') {
        if (-not $Parser.Anchors.Has($Rest.Substring(1))) { Fail "YAML line ${LineNo}: unknown anchor $Rest" 3 }
        return ,(Copy-Node ($Parser.Anchors.Get($Rest.Substring(1))))
    }
    if ($Rest -match '^[|>][+-]?\d*$') { return (Read-BlockScalar $Parser $Rest $ParentIndent) }
    if ($Rest.StartsWith("{") -or $Rest.StartsWith("[")) {
        $flow = Collect-Flow $Parser $Rest $LineNo
        return ,(Read-Flow $flow $LineNo $Parser.Anchors)
    }
    if ($Rest -eq "") { return ,(Read-YamlNested $Parser $ParentIndent) }
    $value = ConvertTo-Scalar $Rest
    $continuation = Read-PlainContinuation $Parser $ParentIndent
    if ($continuation.Count -gt 0 -and $value -is [string]) {
        return (($value + " " + ($continuation -join " ")).Trim())
    }
    return $value
}
function Read-YamlNested($Parser, [int]$ParentIndent) {
    $peeked = Update-ParserPeek $Parser
    if ($null -eq $peeked) { return $null }
    $indent = Get-Indent ([string]$peeked.Text)
    if ($indent -le $ParentIndent) { return $null }
    $content = (Remove-YamlComment ([string]$peeked.Text)).Trim()
    if ($content.StartsWith("-")) { return ,(Read-YamlSequence $Parser $indent) }
    return ,(Read-YamlMapping $Parser $indent)
}
function Read-PlainContinuation($Parser, [int]$ParentIndent) {
    $parts = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $peeked = Update-ParserPeek $Parser
        if ($null -eq $peeked) { break }
        if ((Get-Indent ([string]$peeked.Text)) -le $ParentIndent) { break }
        $stripped = (Remove-YamlComment ([string]$peeked.Text)).Trim()
        if ($stripped.StartsWith("-") -or $script:KeyValue.IsMatch($stripped)) { break }
        $null = Pop-ParserLine $Parser
        $null = $parts.Add($stripped)
    }
    return ,$parts
}
function Read-BlockScalar($Parser, [string]$Header, [int]$ParentIndent) {
    $folded = $Header.StartsWith(">")
    $chomp = "clip"
    if ($Header.Contains("-")) { $chomp = "strip" }
    elseif ($Header.Contains("+")) { $chomp = "keep" }
    $explicit = $null
    $numberMatch = [regex]::Match($Header, '\d+')
    if ($numberMatch.Success) { $explicit = [int]$numberMatch.Value }
    $rawLines = New-Object System.Collections.Generic.List[string]
    while ($Parser.Index -lt $Parser.Lines.Count) {
        $raw = [string]$Parser.Lines[$Parser.Index].Text
        if ($raw.Trim() -eq "") { $null = $rawLines.Add(""); $Parser.Index++; continue }
        if ((Get-Indent $raw) -le $ParentIndent) { break }
        $null = $rawLines.Add($raw)
        $Parser.Index++
    }
    if ($rawLines.Count -eq 0) { return "" }
    $blockIndent = 0
    if ($null -ne $explicit) { $blockIndent = $explicit + $ParentIndent }
    else {
        $blockIndent = 9999
        foreach ($raw in $rawLines) { if ($raw.Trim() -ne "") { $indent = Get-Indent $raw; if ($indent -lt $blockIndent) { $blockIndent = $indent } } }
    }
    $pieces = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $rawLines) {
        if ($raw.Trim() -eq "") { $null = $pieces.Add("") }
        elseif ($raw.Length -ge $blockIndent) { $null = $pieces.Add($raw.Substring($blockIndent)) }
        else { $null = $pieces.Add($raw.TrimStart(" ")) }
    }
    if ($folded) { $text = (Fold-Lines $pieces) } else { $text = ($pieces -join "`n") }
    if ($chomp -eq "keep") { if ($text.EndsWith("`n")) { return $text }; return $text + "`n" }
    return $text.TrimEnd("`n")
}
function Fold-Lines($Pieces) {
    $folded = New-Object System.Collections.Generic.List[string]
    $buffer = New-Object System.Collections.Generic.List[string]
    foreach ($piece in $Pieces) {
        if ($piece -eq "") {
            if ($buffer.Count -gt 0) { $null = $folded.Add(($buffer -join " ")); $buffer.Clear() }
            $null = $folded.Add("")
        } else { $null = $buffer.Add($piece.Trim()) }
    }
    if ($buffer.Count -gt 0) { $null = $folded.Add(($buffer -join " ")) }
    return ($folded -join "`n")
}
function Merge-Map($Mapping, $Value, [int]$LineNo) {
    $sources = New-Object System.Collections.Generic.List[object]
    if (Test-Map $Value) { $null = $sources.Add($Value) }
    elseif (Test-Seq $Value) {
        foreach ($item in $Value) {
            if (-not (Test-Map $item)) { Fail "YAML line ${LineNo}: merge key expects mappings" 3 }
            $null = $sources.Add($item)
        }
    } else { Fail "YAML line ${LineNo}: merge key expects a mapping" 3 }
    foreach ($source in $sources) {
        foreach ($pair in $source) {
            if (-not $Mapping.Has($pair.Key)) { $Mapping.Set($pair.Key, (Copy-Node $pair.Value)) }
        }
    }
}
function Split-Anchor([string]$Text) {
    $match = [regex]::Match($Text.Trim(), '^&([A-Za-z0-9_-]+)\s*(.*)$')
    if (-not $match.Success) { return @{ Name = $null; Rest = $Text } }
    return @{ Name = $match.Groups[1].Value; Rest = $match.Groups[2].Value }
}
function Save-Anchor($Parser, $Name, $Value) {
    if ($null -ne $Name -and $Name -ne "") { $Parser.Anchors.Set([string]$Name, $Value) }
}
function Test-FlowBalanced([string]$Text) {
    $square = 0; $curly = 0; $single = $false; $double = $false; $escaped = $false
    foreach ($char in $Text.ToCharArray()) {
        if ($double) {
            if ($escaped) { $escaped = $false } elseif ($char -eq "\") { $escaped = $true } elseif ($char -eq '"') { $double = $false }
            continue
        }
        if ($single) { if ($char -eq "'") { $single = $false }; continue }
        if ($char -eq '"') { $double = $true }
        elseif ($char -eq "'") { $single = $true }
        elseif ($char -eq "[") { $square++ }
        elseif ($char -eq "]") { $square-- }
        elseif ($char -eq "{") { $curly++ }
        elseif ($char -eq "}") { $curly-- }
        if ($square -lt 0 -or $curly -lt 0) { return $false }
    }
    return ($square -eq 0 -and $curly -eq 0 -and -not $single -and -not $double)
}
function Collect-Flow($Parser, [string]$First, [int]$LineNo) {
    $chunks = New-Object System.Collections.Generic.List[string]
    $null = $chunks.Add($First)
    while (-not (Test-FlowBalanced ($chunks -join "`n"))) {
        if ($Parser.Index -ge $Parser.Lines.Count) { Fail "YAML line ${LineNo}: flow collection ran past the end of the file" 3 }
        $raw = [string]$Parser.Lines[$Parser.Index].Text
        $Parser.Index++
        $null = $chunks.Add((Remove-YamlComment $raw).Trim())
    }
    return ($chunks -join "`n")
}

function Read-Flow([string]$Text, [int]$LineNo, $Anchors) {
    $state = @{ Text = $Text.Trim(); Index = 0; Line = $LineNo; Anchors = $Anchors }
    $value = Read-FlowValue $state
    Skip-FlowSpace $state
    if ($state.Index -lt $state.Text.Length) { Fail "YAML line ${LineNo}: unexpected text after a flow collection" 3 }
    return ,$value
}
function Skip-FlowSpace($State) {
    while ($State.Index -lt $State.Text.Length -and " `t`r`n".Contains([string]$State.Text[$State.Index])) { $State.Index++ }
}
function Read-FlowValue($State) {
    Skip-FlowSpace $State
    if ($State.Index -ge $State.Text.Length) { Fail "YAML line $($State.Line): missing value in a flow collection" 3 }
    $char = $State.Text[$State.Index]
    if ($char -eq "[") { return ,(Read-FlowSequence $State) }
    if ($char -eq "{") { return ,(Read-FlowMapping $State) }
    if ($char -eq "'" -or $char -eq '"') { return (Read-FlowQuoted $State) }
    if ($char -eq "*") { return (Read-FlowAlias $State) }
    return (Read-FlowPlain $State)
}
function Read-FlowSequence($State) {
    $State.Index++
    $items = New-Seq
    while ($true) {
        Skip-FlowSpace $State
        if ($State.Index -ge $State.Text.Length) { Fail "YAML line $($State.Line): sequence is missing a closing bracket" 3 }
        if ($State.Text[$State.Index] -eq "]") { $State.Index++; return ,$items }
        $null = $items.Add((Read-FlowValue $State))
        Skip-FlowSpace $State
        if ($State.Index -ge $State.Text.Length) { Fail "YAML line $($State.Line): sequence is missing a closing bracket" 3 }
        if ($State.Text[$State.Index] -eq ",") { $State.Index++; continue }
        if ($State.Text[$State.Index] -eq "]") { $State.Index++; return ,$items }
        Fail "YAML line $($State.Line): expected a comma in a flow sequence" 3
    }
}
function Read-FlowMapping($State) {
    $State.Index++
    $mapping = New-Map
    while ($true) {
        Skip-FlowSpace $State
        if ($State.Index -ge $State.Text.Length) { Fail "YAML line $($State.Line): mapping is missing a closing brace" 3 }
        if ($State.Text[$State.Index] -eq "}") { $State.Index++; return ,$mapping }
        $key = [string](Read-FlowValue $State)
        Skip-FlowSpace $State
        if ($State.Index -ge $State.Text.Length -or $State.Text[$State.Index] -ne ":") { Fail "YAML line $($State.Line): expected a colon in a flow mapping" 3 }
        $State.Index++
        $value = Read-FlowValue $State
        if ($key -eq "<<") { Merge-Map $mapping $value $State.Line } else { $mapping.Set($key, $value) }
        Skip-FlowSpace $State
        if ($State.Index -ge $State.Text.Length) { Fail "YAML line $($State.Line): mapping is missing a closing brace" 3 }
        if ($State.Text[$State.Index] -eq ",") { $State.Index++; continue }
        if ($State.Text[$State.Index] -eq "}") { $State.Index++; return ,$mapping }
        Fail "YAML line $($State.Line): expected a comma in a flow mapping" 3
    }
}
function Read-FlowQuoted($State) {
    $quote = $State.Text[$State.Index]
    $end = $State.Index + 1
    $chars = New-Object System.Text.StringBuilder
    if ($quote -eq "'") {
        while ($end -lt $State.Text.Length) {
            if ($State.Text[$end] -eq "'") {
                if ($end + 1 -lt $State.Text.Length -and $State.Text[$end + 1] -eq "'") { $null = $chars.Append("'"); $end += 2; continue }
                $State.Index = $end + 1
                return $chars.ToString()
            }
            $null = $chars.Append($State.Text[$end]); $end++
        }
        Fail "YAML line $($State.Line): unterminated single-quoted string" 3
    }
    $escaped = $false
    while ($end -lt $State.Text.Length) {
        $char = $State.Text[$end]
        if ($escaped) {
            $mapping = @{ "n" = "`n"; "t" = "`t"; "r" = "`r"; "\" = "\"; '"' = '"'; "/" = "/" }
            if ($mapping.ContainsKey([string]$char)) { $null = $chars.Append($mapping[[string]$char]) }
            elseif ($char -eq "u" -and $end + 4 -lt $State.Text.Length) {
                $null = $chars.Append([char][Convert]::ToInt32($State.Text.Substring($end + 1, 4), 16)); $end += 4
            } else { Fail "YAML line $($State.Line): unsupported escape" 3 }
            $escaped = $false
        } elseif ($char -eq "\") { $escaped = $true }
        elseif ($char -eq '"') { $State.Index = $end + 1; return $chars.ToString() }
        else { $null = $chars.Append($char) }
        $end++
    }
    Fail "YAML line $($State.Line): unterminated double-quoted string" 3
    return ""
}
function Read-FlowAlias($State) {
    $match = [regex]::Match($State.Text.Substring($State.Index), '^\*([A-Za-z0-9_-]+)')
    if (-not $match.Success) { Fail "YAML line $($State.Line): invalid alias" 3 }
    $name = $match.Groups[1].Value
    $State.Index += $match.Length
    if (-not $State.Anchors.Has($name)) { Fail "YAML line $($State.Line): unknown anchor *$name" 3 }
    return ,(Copy-Node ($State.Anchors.Get($name)))
}
function Read-FlowPlain($State) {
    $start = $State.Index
    while ($State.Index -lt $State.Text.Length -and -not ",[]{}:#`n".Contains([string]$State.Text[$State.Index])) { $State.Index++ }
    $token = $State.Text.Substring($start, $State.Index - $start).Trim()
    if ($token -eq "") { Fail "YAML line $($State.Line): missing plain scalar in a flow collection" 3 }
    return (ConvertTo-Scalar $token)
}

function ConvertFrom-JsonValue($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [string] -or $Value -is [bool] -or (Test-Number $Value)) { return $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $map = New-Map
        foreach ($key in $Value.Keys) { $map.Set([string]$key, (ConvertFrom-JsonValue $Value[$key])) }
        return ,$map
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        $seq = New-Seq
        foreach ($item in $Value) { $null = $seq.Add((ConvertFrom-JsonValue $item)) }
        return ,$seq
    }
    return $Value
}
function Import-JsonText([string]$Text) {
    Add-Type -AssemblyName System.Web.Extensions
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $serializer.MaxJsonLength = 67108864
    $serializer.RecursionLimit = 256
    return ,(ConvertFrom-JsonValue ($serializer.DeserializeObject($Text)))
}
function Import-SpecFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "OpenAPIファイルがありません: $Path" 3 }
    $text = [System.IO.File]::ReadAllText($Path)
    $trimmed = $text.TrimStart()
    try {
        if ([System.IO.Path]::GetExtension($Path).ToLowerInvariant() -eq ".json" -or $trimmed.StartsWith("{")) {
            return ,(Import-JsonText $text)
        }
        return ,(Import-YamlText $text)
    } catch [SpecException] { throw }
    catch { Fail ("定義を読めません: " + $_.Exception.Message) 3 }
}

. (Join-Path $PSScriptRoot "jmx-build.ps1")
if ($MyInvocation.InvocationName -ne ".") {
    $exitCode = Invoke-JmxMain @args
    exit $exitCode
}
