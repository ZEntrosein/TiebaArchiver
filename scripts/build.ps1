param([string]$Python = "")
$ErrorActionPreference = "Stop"
$taskProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
    $Python = Join-Path $taskProjectRoot ".venv\Scripts\python.exe"
}
Push-Location -LiteralPath $taskProjectRoot
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    & $Python -m PyInstaller --noconfirm --onefile --name TiebaScraper_zip_win-x64 --paths src --distpath release --workpath build --specpath build src/cli_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Build failed" }
} finally {
    Pop-Location
}
