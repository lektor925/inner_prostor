<#
.SYNOPSIS
    Запускает manage.py с переменными окружения из EnvFile.

.DESCRIPTION
    Общий launcher для задач Task Scheduler (импорт справочников/остатков) и
    для ручных прогонов на сервере. Задачи планировщика стартуют программу
    напрямую и не видят прод-переменные окружения — этот скрипт подгружает
    их из того же файла, что использует служба (deploy\env.example -> prod.env).

.PARAMETER EnvFile
    Путь к файлу KEY=VALUE. Обязателен.

.PARAMETER ManageArgs
    Всё, что после известных параметров, передаётся в manage.py как есть
    (включая -флаги вроде --kind).

.PARAMETER RepoDir
    Корень репозитория. По умолчанию — родитель папки скрипта.

.PARAMETER VenvDir
    Папка venv. По умолчанию <RepoDir>\.venv.

.EXAMPLE
    .\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env import_1c --kind reference
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    # Всё позиционное уходит сюда (аргументы manage.py, включая --флаги).
    # RepoDir/VenvDir переопределяются только по имени.
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$ManageArgs,

    [string]$RepoDir,

    [string]$VenvDir
)

$ErrorActionPreference = 'Stop'

if (-not $RepoDir) { $RepoDir = Split-Path -Parent $PSScriptRoot }
if (-not $VenvDir) { $VenvDir = Join-Path $RepoDir '.venv' }

# На случай, если вызывающий всё же поставил разделитель "--" — убираем.
if ($ManageArgs.Count -gt 0 -and $ManageArgs[0] -eq '--') {
    $ManageArgs = $ManageArgs[1..($ManageArgs.Count - 1)]
}
if (-not $ManageArgs -or $ManageArgs.Count -eq 0) {
    throw "Не переданы аргументы manage.py (например: ... import_1c --kind reference)."
}

$python = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Не найден $python." }

$manage = Join-Path $RepoDir 'manage.py'
if (-not (Test-Path $manage)) { throw "Не найден $manage." }

if (-not (Test-Path $EnvFile)) { throw "Не найден файл окружения: $EnvFile." }

foreach ($line in Get-Content -Path $EnvFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
    $eq = $trimmed.IndexOf('=')
    if ($eq -lt 1) { continue }
    $key = $trimmed.Substring(0, $eq).Trim()
    $val = $trimmed.Substring($eq + 1).Trim()
    Set-Item -Path "Env:$key" -Value $val
}

Set-Location $RepoDir
& $python $manage @ManageArgs
exit $LASTEXITCODE
