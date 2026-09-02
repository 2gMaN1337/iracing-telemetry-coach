param([string]$Voice = "Katja", [double]$Rate = 1.0)

# Rendert Ansagen in WAV-Dateien. Eingabe je Zeile: "<wav-pfad><TAB><text>",
# Antwort: "OK" oder "ERR". Der Prozess bleibt warm, damit die Ansage direkt
# nach der Zieldurchfahrt kommt.
#
# Bevorzugt die OneCore-Stimmen (Katja/Stefan) ueber WinRT - die klingen
# deutlich natuerlicher als die Desktop-Stimmen und sind ueber System.Speech
# gar nicht erreichbar. Faellt auf System.Speech zurueck, wenn WinRT nicht geht.

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$mode = "winrt"
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

    $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $v = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object { $_.DisplayName -like "*$Voice*" } | Select-Object -First 1
    if ($v) { $synth.Voice = $v }
    $synth.Options.SpeakingRate = $Rate
} catch {
    $mode = "sapi"
}

if ($mode -eq "sapi") {
    Add-Type -AssemblyName System.Speech
    $sapi = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try { $sapi.SelectVoice("Microsoft Hedda Desktop") } catch { }
    # WinRT-Tempo (1.0 = normal) auf den SAPI-Bereich -10..10 abbilden
    $r = [int][Math]::Round(($Rate - 1.0) * 5)
    $sapi.Rate = [Math]::Max(-10, [Math]::Min(10, $r))
    $sapi.Volume = 100
}

function Await($op, $type) {
    $t = $script:asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if (-not $line.Trim()) { continue }

    $parts = $line -split "`t", 2
    if ($parts.Count -lt 2) { continue }
    $path = $parts[0]
    $text = $parts[1].Trim()
    if (-not $text) { continue }

    try {
        if ($mode -eq "winrt") {
            $stream = Await $synth.SynthesizeTextToStreamAsync($text) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
            $size = [uint32]$stream.Size
            $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
            Await $reader.LoadAsync($size) ([uint32]) | Out-Null
            $bytes = New-Object byte[] $size
            $reader.ReadBytes($bytes)
            $reader.Dispose()
            $stream.Dispose()
            [System.IO.File]::WriteAllBytes($path, $bytes)
        } else {
            $sapi.SetOutputToWaveFile($path)
            $sapi.Speak($text)
            $sapi.SetOutputToDefaultAudioDevice()
        }
        Write-Output "OK"
    } catch {
        if ($mode -eq "sapi") { try { $sapi.SetOutputToDefaultAudioDevice() } catch { } }
        Write-Output "ERR"
    }
}
