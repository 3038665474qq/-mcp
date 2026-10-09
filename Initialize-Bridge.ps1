#requires -Version 5.1
[CmdletBinding()]
param([switch]$CheckOnly, [switch]$SkipLogin, [switch]$SkipRegister)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$bridgeRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$setupRoot = Join-Path $bridgeRoot '.setup'
$script:setupLog = $null
$setupLock = $null

function Write-Step([string]$Message) {
    Write-Host $Message
    if ($script:setupLog) { Add-Content -LiteralPath $script:setupLog -Value $Message -Encoding UTF8 }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments 2>&1 | ForEach-Object {
        Write-Host $_
        if ($script:setupLog) { Add-Content -LiteralPath $script:setupLog -Value $_ -Encoding UTF8 }
    }
    if ($LASTEXITCODE -ne 0) { throw "命令失败（退出码 $LASTEXITCODE）：$Executable" }
}

function Refresh-TaskPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Find-Python312 {
    $candidates = @()
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $found = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += [string]$found }
        } catch { }
    }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    $candidates += (Join-Path $env:ProgramFiles 'Python312\python.exe')
    $foundCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($foundCommand -and $foundCommand.Source -notlike '*\WindowsApps\*') { $candidates += $foundCommand.Source }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            try {
                $version = & $candidate -c 'import sys; print(sys.version.split()[0])' 2>$null
                if ($LASTEXITCODE -eq 0 -and $version -like '3.12.*') { return $candidate }
            } catch { }
        }
    }
    return $null
}

function Find-Node {
    $candidates = @()
    $found = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($found) { $candidates += $found.Source }
    $candidates += (Join-Path $env:ProgramFiles 'nodejs\node.exe')
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            try {
                $version = & $candidate -p 'process.versions.node' 2>$null
                if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'22.12.0') { return $candidate }
            } catch { }
        }
    }
    return $null
}

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $chromeVersion = (Get-Item -LiteralPath $candidate).VersionInfo.ProductVersion
            if ($chromeVersion -match '^(\d+)\.' -and [int]$Matches[1] -ge 144) { return $candidate }
        }
    }
    return $null
}

function Install-Package([string]$PackageId, [string]$Scope = '') {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "缺少 WinGet，无法自动安装 $PackageId。请在 Microsoft Store 安装/更新应用安装程序，再双击初始化。官方说明：https://learn.microsoft.com/windows/package-manager/winget/"
    }
    $arguments = @('install', '--id', $PackageId, '--exact', '--source', 'winget', '--silent',
                   '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    if ($Scope) { $arguments += @('--scope', $Scope) }
    Write-Step "正在安装 $PackageId；Windows 可能请求系统安装授权。"
    Invoke-Checked $winget.Source $arguments
    Refresh-TaskPath
}

function Test-CodexLogin([string]$NodeExe, [string]$CliJs) {
    $ErrorActionPreference = 'Continue'
    & $NodeExe $CliJs login status 2>&1 | ForEach-Object { Write-Host $_ }
    return ($LASTEXITCODE -eq 0)
}

try {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw '此安装器支持 Windows 10/11 64 位。Mac、Linux 和 32 位 Windows 不适用。'
    }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw '此迁移包尚未验证 Windows ARM64，请使用 x64 Windows 电脑。'
    }
    if ($bridgeRoot.StartsWith('\\')) { throw '请先将整个文件夹复制到本地磁盘，再运行初始化。' }
    $python = Find-Python312
    $node = Find-Node
    $chrome = Find-Chrome
    if ($CheckOnly) {
        [pscustomobject]@{
            folder = $bridgeRoot; python312 = $python; node = $node; chrome = $chrome
            winget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
            virtualEnvironment = (Test-Path -LiteralPath (Join-Path $bridgeRoot '.venv\Scripts\python.exe'))
            action = '只检查；未安装、未注册、未登录'
        } | ConvertTo-Json
        exit 0
    }
    New-Item -ItemType Directory -Path $setupRoot -Force | Out-Null
    $script:setupLog = Join-Path $setupRoot ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    $setupLockPath = Join-Path $setupRoot 'initialize.lock'
    try { $setupLock = [System.IO.File]::Open($setupLockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw '另一个初始化正在运行，请等待完成后再试。' }
    Write-Step "安装位置：$bridgeRoot"
    if (-not $python) { Install-Package 'Python.Python.3.12' 'user'; $python = Find-Python312 }
    if (-not $node) { Install-Package 'OpenJS.NodeJS.LTS'; $node = Find-Node }
    if (-not $chrome) { Install-Package 'Google.Chrome'; $chrome = Find-Chrome }
    if (-not $python -or -not $node -or -not $chrome) { throw '环境仍未找到。请关闭窗口后重新运行初始化，并查看上方安装错误。' }
    $nodeDirectory = Split-Path -Parent $node
    $env:Path = $nodeDirectory + ';' + $env:Path
    $env:NOTE_BRIDGE_NODE = $node
    $env:PYTHONUTF8 = '1'
    $env:npm_config_cache = Join-Path $setupRoot 'npm-cache'
    $env:PIP_CACHE_DIR = Join-Path $setupRoot 'pip-cache'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $venv = Join-Path $bridgeRoot '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $venvValid = $false
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        try {
            $details = & $venvPython -c 'import sys,json; print(json.dumps([sys.prefix, list(sys.version_info[:2]), sys.base_prefix]))' 2>$null
            if ($LASTEXITCODE -eq 0) {
                $details = $details | ConvertFrom-Json
                $venvValid = ([System.IO.Path]::GetFullPath($details[0]) -eq $venv -and $details[1][0] -eq 3 -and $details[1][1] -eq 12 -and (Test-Path -LiteralPath $details[2]))
            }
        } catch { $venvValid = $false }
    }
    if (-not $venvValid) {
        if (Test-Path -LiteralPath $venv) {
            # Both resolved paths must be immediate children of this installation.
            $resolvedVenv = (Get-Item -LiteralPath $venv -Force).FullName
            if ([System.IO.Path]::GetDirectoryName($resolvedVenv) -ne $bridgeRoot) { throw '虚拟环境路径超出安装目录，已停止。' }
            if ((Get-Item -LiteralPath $venv -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '虚拟环境为链接，已停止自动迁移。' }
            $venvBackup = Join-Path $bridgeRoot ('.venv.backup-' + [guid]::NewGuid().ToString('N'))
            if ([System.IO.Path]::GetDirectoryName($venvBackup) -ne $bridgeRoot) { throw '备份路径超出安装目录。' }
            Move-Item -LiteralPath $venv -Destination $venvBackup
            Write-Step "旧环境已保留：$venvBackup"
        }
        Write-Step '正在创建本机 Python 环境。'
        Invoke-Checked $python @('-m', 'venv', $venv)
    }
    Write-Step '正在安装 Python 依赖。'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $bridgeRoot 'requirements-lock.txt'))
    Invoke-Checked $venvPython @('-m', 'pip', 'check')
    Invoke-Checked $venvPython @('-c', 'import sys; sys.path.insert(0,sys.argv[1]); import tkinter; import yinxiang_server; import publish_html', $bridgeRoot)
    $npmCli = Join-Path $nodeDirectory 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) { throw 'Node.js 安装缺少 npm，请修复 Node.js 后重试。' }
    Write-Step '正在安装 Chrome MCP。'
    Invoke-Checked $node @($npmCli, 'ci', '--prefix', $bridgeRoot, '--ignore-scripts', '--no-audit', '--no-fund')
    $codexRoot = Join-Path $bridgeRoot '.runtime\codex'
    New-Item -ItemType Directory -Path $codexRoot -Force | Out-Null
    $codexJs = Join-Path $codexRoot 'node_modules\@openai\codex\bin\codex.js'
    Write-Step '正在安装本目录专用 Codex CLI。'
    Invoke-Checked $node @($npmCli, 'install', '--prefix', $codexRoot, '--ignore-scripts', '--no-audit', '--no-fund', '@openai/codex@latest')
    Invoke-Checked $node @($codexJs, '--version')
    $execHelp = & $node $codexJs exec --help
    if ($LASTEXITCODE -ne 0 -or ($execHelp -join "`n") -notmatch '--approve-for-me') { throw '安装的 Codex 不支持所需自动审查参数，请查看安装日志。' }
    @{ node = $node; codexJs = $codexJs; root = $bridgeRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $setupRoot 'runtime.json') -Encoding UTF8
    if (-not $SkipRegister) {
        Write-Step '正在注册当前文件夹的 Chrome MCP；已有配置会先备份。'
        Invoke-Checked $venvPython @((Join-Path $bridgeRoot 'configure.py'), '--html-only', '--replace-managed')
    }
    Write-Step '正在检查 MCP 启动；此步不读取浏览器页面。'
    Invoke-Checked $venvPython @((Join-Path $bridgeRoot 'check_connection.py'), '--server', 'chrome_current')
    $loggedIn = Test-CodexLogin $node $codexJs
    if (-not $loggedIn -and -not $SkipLogin) {
        Write-Step '接下来请在打开的网页完成 Codex 登录。'
        Invoke-Checked $node @($codexJs, 'login')
        Invoke-Checked $node @($codexJs, 'login', 'status')
        $loggedIn = $true
    }
    Write-Step '环境初始化完成。'
    if (-not $loggedIn) { Write-Step '尚未登录 Codex。请双击“登录Codex.bat”完成登录。' }
    if ($SkipRegister) { Write-Step '测试模式跳过了 MCP 注册；正式使用请重新运行默认初始化。' }
    Write-Step '下一步：Chrome 登录知乎/CSDN，在 chrome://inspect/#remote-debugging 开启远程调试并允许连接。'
    Write-Step '然后将单篇 HTML 拖到“保存草稿.bat”，核对后再使用“正式发布.bat”。'
    Write-Step "日志：$script:setupLog"
    exit 0
} catch {
    Write-Host ("初始化停止：" + $_.Exception.Message) -ForegroundColor Red
    if ($script:setupLog) { Add-Content -LiteralPath $script:setupLog -Value $_.Exception.ToString() -Encoding UTF8 }
    exit 1
} finally {
    if ($null -ne $setupLock) { $setupLock.Dispose() }
}
