<#
  微信群日报 - 无人值守安装
  由 setup_silent.bat（豆包用）或 setup.bat（人双击）调用。
  只用国内镜像，不弹确认，不需要管理员；最后一行输出 JSON。
#>
param(
    [string]$PipMirror = 'https://pypi.tuna.tsinghua.edu.cn/simple',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'bootstrap.log'
try { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null } catch {}

function Say([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
    try { Add-Content -Path $Log -Value $line -Encoding UTF8 } catch {}
}

function Finish($Data, [int]$Code) {
    if ($Data -is [System.Collections.IDictionary]) { $Data['ok'] = ($Code -eq 0) }
    Say ($Data | ConvertTo-Json -Depth 4 -Compress)
    exit $Code
}

function Fail([string]$Code, [string]$Message, [string]$Hint, [int]$ExitCode) {
    Say "失败[$Code]：$Message"
    Finish ([ordered]@{
        error_code = $Code
        error = $Message
        hint = $Hint
        log = $Log
    }) $ExitCode
}

function Get-Python312 {
    $paths = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $out = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { $paths.Add(([string]$out).Trim()) }
        } catch {}
    }
    foreach ($name in @('python3.12', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -like '*\WindowsApps\*') { continue }
        try {
            $pair = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and ([string]$pair).Trim() -eq '3.12') {
                $paths.Add((& $cmd.Source -c "import sys; print(sys.executable)" 2>$null).Trim())
            }
        } catch {}
    }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        'C:\Python312\python.exe',
        'C:\Program Files\Python312\python.exe'
    )) {
        if (Test-Path $candidate) { $paths.Add($candidate) }
    }
    foreach ($path in $paths) {
        if (-not $path -or -not (Test-Path $path)) { continue }
        try {
            $version = & $path -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq '3.12') { return $path.Trim() }
        } catch {}
    }
    return $null
}

function Install-Python312 {
    $versions = @('3.12.10', '3.12.9', '3.12.8')
    $mirrors = @(
        'https://registry.npmmirror.com/-/binary/python/{0}/python-{0}-amd64.exe',
        'https://mirrors.huaweicloud.com/python/{0}/python-{0}-amd64.exe',
        'https://www.python.org/ftp/python/{0}/python-{0}-amd64.exe'
    )
    $temp = Join-Path $env:TEMP ('wechat-daily-python-{0}.exe' -f (Get-Date -Format 'yyyyMMddHHmmss'))
    foreach ($version in $versions) {
        foreach ($pattern in $mirrors) {
            $url = $pattern -f $version
            Say "下载 Python $version：$url"
            try {
                if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                    & curl.exe -L --fail --retry 2 --connect-timeout 20 --max-time 600 --silent --show-error -o $temp $url
                    if ($LASTEXITCODE -ne 0) { throw "curl 退出码 $LASTEXITCODE" }
                } else {
                    Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing -TimeoutSec 600
                }
                if (-not (Test-Path $temp) -or (Get-Item $temp).Length -lt 10MB) { throw '安装包不完整' }
                $signature = Get-AuthenticodeSignature -FilePath $temp
                if ($signature.Status -ne 'Valid') { throw "安装包签名无效：$($signature.Status)" }
                Unblock-File -Path $temp -ErrorAction SilentlyContinue
                Say "静默安装 Python $version（当前用户，不需要管理员）..."
                $process = Start-Process -FilePath $temp `
                    -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0' `
                    -Wait -PassThru
                Say "安装器退出码：$($process.ExitCode)"
                Remove-Item $temp -Force -ErrorAction SilentlyContinue
                for ($i = 0; $i -lt 15; $i++) {
                    $found = Get-Python312
                    if ($found) { return $found }
                    Start-Sleep -Seconds 2
                }
            } catch {
                Say "这个源不可用：$($_.Exception.Message)"
                if (Test-Path $temp) { Remove-Item $temp -Force -ErrorAction SilentlyContinue }
            }
        }
    }
    return $null
}

try {
    Say '开始检查安装环境'
    $python = Get-Python312
    if (-not $python) {
        Say '本机没有 Python 3.12，开始自动安装'
        $python = Install-Python312
    }
    if (-not $python) {
        Fail 'python_missing' '自动安装 Python 3.12 失败' `
            '检查网络后重试；也可以手动安装 Python 3.12（勾选 Add python.exe to PATH）再运行 setup_silent.bat' 10
    }
    Say "使用 Python：$python"

    $venv = Join-Path $Root '.venv'
    $venvPy = Join-Path $venv 'Scripts\python.exe'
    $recreate = -not (Test-Path $venvPy)
    if (-not $recreate) {
        try {
            $venvVersion = & $venvPy -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if (([string]$venvVersion).Trim() -ne '3.12') { $recreate = $true }
        } catch { $recreate = $true }
    }
    if ($Force -and (Test-Path $venv)) { $recreate = $true }
    if ($recreate -and (Test-Path $venv)) {
        $backup = Join-Path $Root ('.venv_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
        Say "旧虚拟环境改名为 $backup"
        Move-Item -Path $venv -Destination $backup -Force
    }
    if (-not (Test-Path $venvPy)) {
        Say '创建虚拟环境 .venv'
        & $python -m venv $venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
            Fail 'venv_failed' '创建虚拟环境失败' '确认磁盘可写、杀毒软件没有拦截，然后重试' 11
        }
    }

    Say "配置国内 pip 源：$PipMirror"
    & $venvPy -m pip config --site set global.index-url $PipMirror 2>$null | Out-Null
    & $venvPy -m pip config --site set global.timeout 60 2>$null | Out-Null

    $mirrors = @($PipMirror, 'https://mirrors.aliyun.com/pypi/simple/', 'https://pypi.tuna.tsinghua.edu.cn/simple/') |
        Select-Object -Unique
    $installed = $false
    foreach ($mirror in $mirrors) {
        Say "安装依赖 wxauto4==41.1.7、openpyxl（源：$mirror）"
        & $venvPy -m pip install --upgrade pip --index-url $mirror --disable-pip-version-check --no-input 2>&1 |
            ForEach-Object { Write-Host $_ }
        & $venvPy -m pip install -r (Join-Path $Root 'requirements.txt') --index-url $mirror `
            --disable-pip-version-check --no-input --progress-bar off 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            break
        }
    }
    if (-not $installed) {
        Fail 'pip_failed' '依赖安装失败' `
            '检查网络；可换阿里云源重试：setup_silent.bat -PipMirror https://mirrors.aliyun.com/pypi/simple/' 12
    }

    Say '验证依赖'
    $verify = & $venvPy -c "import wxauto4, openpyxl; print('ok')" 2>&1
    $verifyText = ($verify | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $verifyText -ne 'ok') {
        Fail 'verify_failed' "依赖装上了但导入失败：$verifyText" `
            '可能是杀毒软件拦截或 Python 环境损坏；删掉 .venv 后重试' 13
    }

    $groupsPath = Join-Path $Root 'config\groups.json'
    $examplePath = Join-Path $Root 'config\groups.example.json'
    if (-not (Test-Path $groupsPath)) {
        Copy-Item -Path $examplePath -Destination $groupsPath
        Say '已创建 config\groups.json（占位群名，需要替换）'
    }
    $groupCount = 0
    $expected = 0
    $groupsReady = $false
    try {
        $cfg = Get-Content -Path $groupsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $groupCount = @($cfg.groups).Count
        $expected = [int]($cfg.expected_groups)
        $groupsReady = $groupCount -gt 0
        foreach ($group in @($cfg.groups)) {
            if (-not $group.name -or $group.name -like '*请替换*') { $groupsReady = $false }
            foreach ($publisher in @($group.publishers)) {
                if ($publisher -like '*请填*') { $groupsReady = $false }
            }
        }
    } catch { $groupsReady = $false }

    $wechat = '未运行'
    try {
        $proc = Get-Process Weixin, WeChat -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($proc) {
            $wechat = (Get-Item $proc.Path).VersionInfo.FileVersion
            if (-not $wechat) { $wechat = '未知' }
        }
    } catch {}

    $pythonVersion = (& $venvPy -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" | Out-String).Trim()
    $wxVersion = (& $venvPy -c "import importlib.metadata as m; print(m.version('wxauto4'))" | Out-String).Trim()
    $oxVersion = (& $venvPy -c "import importlib.metadata as m; print(m.version('openpyxl'))" | Out-String).Trim()

    Say '安装完成'
    Finish ([ordered]@{
        python = $python
        python_version = $pythonVersion
        venv = $venvPy
        wxauto4 = $wxVersion
        openpyxl = $oxVersion
        groups = $groupCount
        expected_groups = $expected
        groups_ready = $groupsReady
        wechat = $wechat
        pip_mirror = $PipMirror
        next = '填 config/groups.json 后运行 status.py'
    }) 0
} catch {
    Fail 'unexpected' $_.Exception.Message '把这段错误和 logs\bootstrap.log 发给技术支持' 99
}
