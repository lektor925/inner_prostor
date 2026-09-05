<#
.SYNOPSIS
    Бэкап БД справочника через pg_dump в файл с меткой времени, с прунингом
    старых копий и записью результата в лог.

.DESCRIPTION
    Шаг 7 спеки деплоя (ADR-013: хранить ~30 копий, класть на тот же
    сетевой ресурс, что и бэкап 1С). Формат дампа — custom (-Fc),
    восстановление через pg_restore (см. deploy\RUNBOOK.md).

    Креды БД берутся из того же EnvFile, что использует служба.

.PARAMETER EnvFile
    Путь к файлу KEY=VALUE (DATABASE_NAME/USER/PASSWORD/HOST/PORT). Обязателен.

.PARAMETER BackupDir
    Куда складывать дампы. Обязателен. UNC-путь к сетевой шаре или локальная
    папка. Учётная запись задачи должна иметь право записи.

.PARAMETER KeepDays
    Удалять дампы старше стольких дней. По умолчанию 30 (ADR-013).

.PARAMETER PgDumpPath
    Путь к pg_dump.exe. По умолчанию "pg_dump" (ищется в PATH).

.EXAMPLE
    .\deploy\backup.ps1 -EnvFile C:\spravochnik\prod.env -BackupDir \\nas\backup\spravochnik
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [Parameter(Mandatory = $true)]
    [string]$BackupDir,

    [int]$KeepDays = 30,

    [string]$PgDumpPath = 'pg_dump'
)

$ErrorActionPreference = 'Stop'

# --- Лог ------------------------------------------------------------
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}
$logFile = Join-Path $BackupDir 'backup.log'
function Write-Log {
    param([string]$Level, [string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $logFile -Value "$stamp [$Level] $Message"
    Write-Host "[$Level] $Message"
}

try {
    if (-not (Test-Path $EnvFile)) { throw "Не найден файл окружения: $EnvFile." }

    $cfg = @{}
    foreach ($line in Get-Content -Path $EnvFile) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $cfg[$trimmed.Substring(0, $eq).Trim()] = $trimmed.Substring($eq + 1).Trim()
    }

    $dbName = $cfg['DATABASE_NAME']
    if (-not $dbName) { throw "В $EnvFile нет DATABASE_NAME." }
    $dbUser = if ($cfg['DATABASE_USER']) { $cfg['DATABASE_USER'] } else { 'postgres' }
    $dbHost = if ($cfg['DATABASE_HOST']) { $cfg['DATABASE_HOST'] } else { 'localhost' }
    $dbPort = if ($cfg['DATABASE_PORT']) { $cfg['DATABASE_PORT'] } else { '5432' }

    $pgDump = (Get-Command $PgDumpPath -ErrorAction SilentlyContinue)
    if (-not $pgDump) { throw "pg_dump не найден ($PgDumpPath). Добавить в PATH папку bin PostgreSQL." }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $outFile = Join-Path $BackupDir "spravochnik-$stamp.dump"

    $env:PGPASSWORD = $cfg['DATABASE_PASSWORD']
    try {
        & $pgDump.Source '-Fc' '-h' $dbHost '-p' $dbPort '-U' $dbUser '-f' $outFile $dbName
        $code = $LASTEXITCODE
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { throw "pg_dump вернул код $code." }
    if (-not (Test-Path $outFile)) { throw "pg_dump отработал, но файла $outFile нет." }

    $sizeMb = [math]::Round((Get-Item $outFile).Length / 1MB, 2)
    if ($sizeMb -le 0) { throw "Файл дампа $outFile пустой." }
    Write-Log 'OK' "Создан $([System.IO.Path]::GetFileName($outFile)), $sizeMb МБ."

    # --- Прунинг ---------------------------------------------------
    $cutoff = (Get-Date).AddDays(-$KeepDays)
    $old = Get-ChildItem -Path $BackupDir -Filter 'spravochnik-*.dump' |
        Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($f in $old) {
        Remove-Item $f.FullName -Force
        Write-Log 'PRUNE' "Удалён старый дамп $($f.Name)."
    }
    Write-Log 'DONE' "Бэкап завершён. Дампов в папке: $((Get-ChildItem -Path $BackupDir -Filter 'spravochnik-*.dump').Count)."
    exit 0
}
catch {
    Write-Log 'ERROR' $_.Exception.Message
    exit 1
}
