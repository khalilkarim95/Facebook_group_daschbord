<#
.SYNOPSIS
    Ein Suchdurchgang mit fester Portion: hoechstens 10 neue Anfragen an Serper.

.DESCRIPTION
    Fuehrt genau einen Durchgang aus und exportiert danach nach Excel und CSV.
    Bereits beantwortete Anfragen liegen dauerhaft im Anfragespeicher und
    kosten nie wieder etwas - die Portion gilt also immer fuer NEUE Anfragen.
    Wiederholtes Aufrufen arbeitet die geplanten Anfragen Stueck fuer Stueck ab.

.PARAMETER Anfragen
    Groesse der Portion. Standard 10 (= hoechstens 10 Credits).

.PARAMETER Plan
    Zeigt nur, was der naechste Durchgang tun wuerde. Fragt nichts ab.

.PARAMETER Oeffnen
    Oeffnet die erzeugte Excel-Datei danach.

.EXAMPLE
    .\suche.ps1
    .\suche.ps1 -Plan
    .\suche.ps1 -Anfragen 5 -Oeffnen
#>

[CmdletBinding()]
param(
    [ValidateRange(0, 50)]
    [int]$Anfragen = 10,
    [switch]$Plan,
    [switch]$Oeffnen
)

$ErrorActionPreference = "Stop"

# Arabische Ausgabe braucht UTF-8; ohne feste Breite bricht Rich die Tabellen um.
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:COLUMNS) { $env:COLUMNS = "140" }

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Python-Umgebung fehlt: $py" -ForegroundColor Red
    Write-Host "Anlegen mit:  python -m venv .venv;  .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
    exit 1
}

# Der Schluessel gehoert ausschliesslich in .env. Hier wird nur geprueft, DASS
# einer da ist - der Wert selbst wird nie ausgegeben.
$envDatei = Join-Path $PSScriptRoot ".env"
$hatSchluessel = $false
if (Test-Path $envDatei) {
    $hatSchluessel = [bool](Select-String -Path $envDatei -Pattern '^\s*SERPER_API_KEY\s*=\s*\S' -Quiet)
}
if (-not $hatSchluessel) {
    Write-Host "SERPER_API_KEY fehlt in .env - es wird nichts abgefragt." -ForegroundColor Red
    Write-Host "Vorlage: .env.example nach .env kopieren und den Schluessel eintragen."
    Write-Host "Ohne Schluessel funktionieren weiterhin:  import-seeds, report, export, search --dry-run"
    exit 1
}

Push-Location $PSScriptRoot
try {
    if ($Plan) {
        Write-Host "`n--- Plan des naechsten Durchgangs (kein Abruf, keine Kosten) ---`n" -ForegroundColor Cyan
        & $py -m fbgroups.cli search --dry-run --provider serper --limit $Anfragen --kurz
        exit $LASTEXITCODE
    }

    Write-Host "`n--- Durchgang: hoechstens $Anfragen neue Anfragen (= $Anfragen Credits) ---`n" -ForegroundColor Cyan
    & $py -m fbgroups.cli search --provider serper --limit $Anfragen
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Der Suchlauf wurde nicht sauber beendet - es wird nicht exportiert." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Write-Host "`n--- Export ---`n" -ForegroundColor Cyan
    & $py -m fbgroups.cli export --format both
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "`n--- Was noch offen ist ---`n" -ForegroundColor Cyan
    & $py -m fbgroups.cli search --dry-run --provider serper --limit $Anfragen --kurz

    $neuste = Get-ChildItem (Join-Path $PSScriptRoot "data\exports\*.xlsx") |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($neuste) {
        Write-Host "`nErgebnisdatei: $($neuste.FullName)" -ForegroundColor Green
        if ($Oeffnen) { Invoke-Item $neuste.FullName }
    }

    Write-Host "Naechste Portion:  .\suche.ps1" -ForegroundColor DarkGray
}
finally {
    Pop-Location
}
