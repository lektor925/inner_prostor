<#
.SYNOPSIS
    Устанавливает/переустанавливает Windows-службу справочника номенклатуры:
    Waitress (WSGI) под управлением NSSM. Идемпотентно — можно запускать
    повторно после обновления кода или правки .env.

.DESCRIPTION
    Шаг 7 спеки деплоя. Nginx/TLS вне объёма — Waitress слушает HTTP
    напрямую в локальной сети.

    Скрипт НЕ хранит секреты: читает переменные окружения из отдельного
    файла (-EnvFile), которого нет в git, и прокидывает их службе через
    NSSM AppEnvironmentExtra.

.PARAMETER EnvFile
    Путь к файлу KEY=VALUE (по образцу deploy\env.example). Обязателен.

.PARAMETER RepoDir
    Корень репозитория. По умолчанию — родитель папки этого скрипта.

.PARAMETER VenvDir
    Папка виртуального окружения. По умолчанию <RepoDir>\.venv.

.PARAMETER Port
    TCP-порт Waitress. По умолчанию 8000.

.PARAMETER ServiceName
    Имя службы Windows. По умолчанию "spravochnik".

.PARAMETER LogDir
    Куда писать stdout/stderr службы. По умолчанию <RepoDir>\logs.

.PARAMETER NssmPath
    Путь к nssm.exe. По умолчанию "nssm" (ищется в PATH).

.EXAMPLE
    .\deploy\install_service.ps1 -EnvFile C:\spravochnik\prod.env
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [string]$RepoDir = (Split-Path -Parent $PSScriptRoot),

    [string]$VenvDir,

    [int]$Port = 8000,

    [string]$ServiceName = 'spravochnik',

    [string]$LogDir,

    [string]$NssmPath = 'nssm'
)

$ErrorActionPreference = 'Stop'

if (-not $VenvDir) { $VenvDir = Join-Path $RepoDir '.venv' }
if (-not $LogDir)  { $LogDir  = Join-Path $RepoDir 'logs' }

# --- Проверки предусловий -------------------------------------------------
$nssm = (Get-Command $NssmPath -ErrorAction SilentlyContinue)
if (-not $nssm) {
    throw "nssm не найден ($NssmPath). Установить: winget install NSSM.NSSM, затем перезайти в консоль."
}
$nssmExe = $nssm.Source

$waitress = Join-Path $VenvDir 'Scripts\waitress-serve.exe'
if (-not (Test-Path $waitress)) {
    throw "Не найден $waitress. Сначала создать venv и pip install -r requirements.txt."
}

if (-not (Test-Path $EnvFile)) {
    throw "Не найден файл окружения: $EnvFile (образец — deploy\env.example)."
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# --- Разбор EnvFile в пары KEY=VALUE ------------------------------------
$envPairs = @()
foreach ($line in Get-Content -Path $EnvFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
    $eq = $trimmed.IndexOf('=')
    if ($eq -lt 1) {
        Write-Warning "Пропущена строка без '=': $line"
        continue
    }
    $key = $trimmed.Substring(0, $eq).Trim()
    $val = $trimmed.Substring($eq + 1).Trim()
    $envPairs += "$key=$val"
}
if ($envPairs.Count -eq 0) {
    throw "В $EnvFile не найдено ни одной пары KEY=VALUE."
}

# --- Создание/остановка службы ----------------------------------------
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Служба '$ServiceName' уже есть — останавливаю и переконфигурирую."
    if ($existing.Status -ne 'Stopped') {
        & $nssmExe stop $ServiceName | Out-Null
    }
} else {
    Write-Host "Создаю службу '$ServiceName'."
    & $nssmExe install $ServiceName $waitress
    if ($LASTEXITCODE -ne 0) { throw "nssm install завершился с кодом $LASTEXITCODE." }
}

# --- Конфигурация (применяется и при первой установке, и при обновлении) ---
# config.wsgi:application — готовый WSGI-объект (не фабрика), поэтому без --call.
$appParams = "--host=0.0.0.0 --port=$Port config.wsgi:application"

& $nssmExe set $ServiceName Application $waitress            | Out-Null
& $nssmExe set $ServiceName AppParameters $appParams         | Out-Null
& $nssmExe set $ServiceName AppDirectory $RepoDir            | Out-Null
& $nssmExe set $ServiceName Start SERVICE_AUTO_START         | Out-Null
& $nssmExe set $ServiceName AppExit Default Restart          | Out-Null
& $nssmExe set $ServiceName AppStdout (Join-Path $LogDir 'service.out.log') | Out-Null
& $nssmExe set $ServiceName AppStderr (Join-Path $LogDir 'service.err.log') | Out-Null
& $nssmExe set $ServiceName AppRotateFiles 1                 | Out-Null
& $nssmExe set $ServiceName AppRotateOnline 1                | Out-Null
& $nssmExe set $ServiceName AppRotateBytes 10485760          | Out-Null

# AppEnvironmentExtra перезаписывается целиком набором пар KEY=VALUE.
& $nssmExe set $ServiceName AppEnvironmentExtra $envPairs    | Out-Null

# --- Запуск и проверка -----------------------------------------------
& $nssmExe start $ServiceName | Out-Null
Start-Sleep -Seconds 3

$svc = Get-Service -Name $ServiceName
Write-Host "Служба '$ServiceName': $($svc.Status)"
if ($svc.Status -ne 'Running') {
    throw "Служба не в состоянии Running. Смотреть $LogDir\service.err.log."
}

try {
    $resp = Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 10
    Write-Host "HTTP localhost:$Port -> $($resp.StatusCode)"
} catch {
    Write-Warning "Служба Running, но HTTP-проверка не прошла: $($_.Exception.Message)"
    Write-Warning "Проверить DJANGO_ALLOWED_HOSTS в $EnvFile и $LogDir\service.err.log."
}

Write-Host "Готово."
