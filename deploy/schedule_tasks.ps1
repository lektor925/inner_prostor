<#
.SYNOPSIS
    Регистрирует три задачи Task Scheduler справочника номенклатуры:
    импорт справочников, импорт остатков, бэкап БД. Идемпотентно —
    существующие задачи с теми же именами перерегистрируются.

.DESCRIPTION
    Шаг 7 спеки деплоя. Расписание соответствует ADR-009:
    - справочники: раз в сутки ночью;
    - остатки: раз в несколько часов в рабочее время;
    - бэкап: раз в сутки ночью, после импорта справочников.

.PARAMETER EnvFile
    Путь к файлу KEY=VALUE прод-окружения. Обязателен.

.PARAMETER RunAsUser
    Учётная запись, под которой выполняются задачи импорта (нужен доступ на
    чтение к папке выгрузки 1С, ADR-009). Рекомендуется отдельный служебный
    пользователь, НЕ личная учётка. Формат DOMAIN\user или .\user.
    Если не указан — задачи регистрируются под текущим пользователем и это
    надо поправить вручную в планировщике.

.PARAMETER RunAsPassword
    Пароль RunAsUser (SecureString). Если не задан и RunAsUser указан —
    скрипт запросит интерактивно.

.PARAMETER RepoDir
    Корень репозитория. По умолчанию — родитель папки скрипта.

.PARAMETER Prefix
    Префикс имён задач. По умолчанию "Spravochnik".

.EXAMPLE
    .\deploy\schedule_tasks.ps1 -EnvFile C:\spravochnik\prod.env -RunAsUser CORP\svc-spravochnik
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [string]$RunAsUser,

    [System.Security.SecureString]$RunAsPassword,

    [string]$RepoDir = (Split-Path -Parent $PSScriptRoot),

    [string]$Prefix = 'Spravochnik'
)

$ErrorActionPreference = 'Stop'

$EnvFile = (Resolve-Path $EnvFile).Path
$RepoDir = (Resolve-Path $RepoDir).Path
$runManage = Join-Path $RepoDir 'deploy\run_manage.ps1'
$backup    = Join-Path $RepoDir 'deploy\backup.ps1'
foreach ($p in @($runManage, $backup)) {
    if (-not (Test-Path $p)) { throw "Не найден $p." }
}

$psExe = Join-Path $PSHOME 'powershell.exe'

# --- Учётная запись выполнения ---------------------------------------
if ($RunAsUser) {
    if (-not $RunAsPassword) {
        $RunAsPassword = (Get-Credential -UserName $RunAsUser `
            -Message "Пароль для задач импорта").Password
    }
    $plainPwd = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($RunAsPassword))
    $principalArgs = @{ User = $RunAsUser; RunLevel = 'Limited' }
} else {
    Write-Warning ("RunAsUser не указан — задачи создаются под '$env:USERNAME'. " +
        "После прогона задать в планировщике служебную учётку с доступом к папке 1С.")
    $principalArgs = @{ User = "$env:USERDOMAIN\$env:USERNAME"; RunLevel = 'Limited' }
}

function Register-SpravochnikTask {
    param(
        [string]$Name,
        [Microsoft.Management.Infrastructure.CimInstance[]]$Triggers,
        [string[]]$ScriptArgs
    )

    $argLine = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File') + $ScriptArgs
    $action = New-ScheduledTaskAction -Execute $psExe -Argument ($argLine -join ' ') -WorkingDirectory $RepoDir
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "Перерегистрирую задачу $Name."
    } else {
        Write-Host "Создаю задачу $Name."
    }

    $register = @{
        TaskName = $Name
        Action   = $action
        Trigger  = $Triggers
        Settings = $settings
    }
    if ($RunAsUser) {
        $register.User     = $RunAsUser
        $register.Password = $plainPwd
        $register.RunLevel = 'Limited'
    } else {
        $register.User     = $principalArgs.User
        $register.RunLevel = 'Limited'
    }
    Register-ScheduledTask @register | Out-Null
}

# --- 1. Импорт справочников: ежедневно 03:00 (ADR-009) ---------------
Register-SpravochnikTask -Name "$Prefix-ImportReference" `
    -Triggers (New-ScheduledTaskTrigger -Daily -At '03:00') `
    -ScriptArgs @("`"$runManage`"", '-EnvFile', "`"$EnvFile`"", 'import_1c', '--kind', 'reference')

# --- 2. Импорт остатков: каждые 3 часа в окне 07:00-19:00 (ADR-009) --
$stockTrigger = New-ScheduledTaskTrigger -Once -At '07:00' `
    -RepetitionInterval (New-TimeSpan -Hours 3) `
    -RepetitionDuration (New-TimeSpan -Hours 12)
Register-SpravochnikTask -Name "$Prefix-ImportStock" `
    -Triggers $stockTrigger `
    -ScriptArgs @("`"$runManage`"", '-EnvFile', "`"$EnvFile`"", 'import_1c', '--kind', 'stock')

# --- 3. Бэкап БД: ежедневно 03:30, после импорта справочников -------
Register-SpravochnikTask -Name "$Prefix-Backup" `
    -Triggers (New-ScheduledTaskTrigger -Daily -At '03:30') `
    -ScriptArgs @("`"$backup`"", '-EnvFile', "`"$EnvFile`"")

Write-Host ""
Write-Host "Зарегистрированы задачи:"
Get-ScheduledTask -TaskName "$Prefix-*" |
    Select-Object TaskName, State |
    Format-Table -AutoSize

Write-Host "Проверка ручным прогоном, например:"
Write-Host "  Start-ScheduledTask -TaskName $Prefix-Backup"
Write-Host "  Get-ScheduledTaskInfo -TaskName $Prefix-Backup   # LastTaskResult должен быть 0"
