param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start'
)

# 鲸鱼娘机器人 - 本地启动/停止/状态 控制脚本
# 路径全部基于脚本自身目录（%~dp0 等价物），可整体搬移。
# 2026-10-20：启动后校验 success / 微信是否真的 connected，失败自动重试一次，
#             并把真实原因打出来（原来不看返回值，没起来也显示"已发送"）。

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$vpy = Join-Path $root '.venv\Scripts\python.exe'
$api = 'http://127.0.0.1:8080/api'
$web = 'http://127.0.0.1:8080/'

function Pause-IfInteractive([string]$message) {
    if (-not [Console]::IsInputRedirected) { Read-Host $message | Out-Null }
}

function Get-BotProcs {
    $pat = Join-Path $root '.venv\Scripts\python*.exe'
    Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like $pat }
}

function Test-Web {
    try {
        $r = Invoke-WebRequest "$api/status" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Wait-Web([int]$seconds = 120) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Web) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Show-WeChatProcess {
    # 只查进程，不做 UI 附着：
    #  1) 机器人用的是 wechatauto 后端，拿 wxauto4 预探测不对路；
    #  2) 预探测会和稍后启动的机器人抢同一个微信窗口。
    # 真正的连接状态以启动后的 /api/status 为准。
    $p = @(Get-Process Weixin, WeChat -ErrorAction SilentlyContinue)
    if ($p.Count -gt 0) {
        $names = ($p | Select-Object -ExpandProperty ProcessName -Unique) -join ', '
        Write-Host ('    检测到微信进程: {0}（{1} 个）' -f $names, $p.Count)
    } else {
        Write-Host '    [提示] 未检测到微信进程，请先打开并登录微信桌面版。' -ForegroundColor Yellow
    }
}

function Get-StatusJson {
    # 必须手工按 UTF-8 解码响应体：
    # PowerShell 5.1 的 Invoke-RestMethod 对无 charset 的 UTF-8 JSON
    # 会按本地代码页解，中文群名会变成乱码。
    $r = Invoke-WebRequest "$api/status" -UseBasicParsing -TimeoutSec 15
    $text = [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
    return ($text | ConvertFrom-Json)
}

function Show-Status {
    Write-Host ''
    Write-Host '──────── 运行状态 ────────' -ForegroundColor Cyan
    $procs = @(Get-BotProcs)
    Write-Host ("  机器人进程 : {0} 个" -f $procs.Count)
    if (Test-Web) {
        try {
            $st = Get-StatusJson
            Write-Host ("  Web 控制台 : 在线  {0}" -f $web)
            Write-Host ("  机器人运行 : {0}" -f $st.running)
            Write-Host ("  微信通道   : {0}" -f $st.wechat.status)
            $groups = @($st.wechat.target_groups)
            Write-Host ("  监控群     : {0}" -f (($groups -join ', ') -replace '^$', '(未配置)'))
            Write-Host ("  私聊接管   : {0}" -f $st.wechat.listen_private)
        } catch {
            Write-Host ("  Web 控制台 : 在线（状态读取失败: {0}）" -f $_.Exception.Message)
        }
    } else {
        Write-Host '  Web 控制台 : 未运行'
    }
    Write-Host '──────────────────────────' -ForegroundColor Cyan
}

function Invoke-StartBot {
    # 返回 $true 表示：接口 success 且微信通道真的 connected
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $resp = $null
        try {
            $resp = Invoke-RestMethod -Method Post -Uri "$api/bot/start" `
                -ContentType 'application/json' -Body '{}' -TimeoutSec 180
        } catch {
            Write-Host ('      启动请求失败: ' + $_.Exception.Message) -ForegroundColor Red
        }

        if ($resp -and $resp.success) {
            Write-Host '      接口返回成功，正在等待微信通道连接...'
            for ($i = 1; $i -le 15; $i++) {
                Start-Sleep -Seconds 2
                try {
                    $st = Get-StatusJson
                    if ($st.running -and $st.wechat.status -eq 'connected') {
                        Write-Host '      微信通道已连接，开始监听。' -ForegroundColor Green
                        return $true
                    }
                } catch {}
            }
            Write-Host '      [警告] 30 秒内没连上微信，请看 logs\chatbot.log' -ForegroundColor Yellow
        } elseif ($resp) {
            Write-Host ('      未启动：{0}' -f $resp.message) -ForegroundColor Yellow
        }

        if ($attempt -lt 2) {
            Write-Host '      4 秒后重试一次...'
            Start-Sleep -Seconds 4
        }
    }
    return $false
}

switch ($Action) {

    'stop' {
        Write-Host '[停止] 正在关闭鲸鱼娘...' -ForegroundColor Yellow
        if (Test-Web) {
            try { Invoke-RestMethod -Method Post -Uri "$api/bot/stop" -ContentType 'application/json' -Body '{}' -TimeoutSec 60 | Out-Null } catch {}
        }
        $procs = @(Get-BotProcs)
        if ($procs.Count -eq 0) {
            Write-Host '  没有发现正在运行的机器人进程。'
        } else {
            foreach ($p in $procs) {
                Write-Host ("  结束进程 PID {0}" -f $p.Id)
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 2
        }
        Show-Status
    }

    'status' {
        Show-Status
    }

    'start' {
        Write-Host ''
        Write-Host '============================================' -ForegroundColor Green
        Write-Host '   鲸鱼娘 · 微信群聊机器人   启动器' -ForegroundColor Green
        Write-Host '============================================' -ForegroundColor Green
        Write-Host ''

        if (-not (Test-Path $vpy)) {
            Write-Host '[错误] 找不到 .venv，请先双击「一键安装所有依赖.bat」完成安装。' -ForegroundColor Red
            Pause-IfInteractive '按回车退出'
            exit 1
        }

        if (Test-Web) {
            Write-Host '[1/2] Web 控制台已在运行，跳过启动。'
        } else {
            Write-Host '[1/2] 检查微信进程...'
            Show-WeChatProcess

            # 微信重启 / 升级后，Qt 无障碍闸门字节（gate byte）会归零，UIA 树整片消失，
            # 机器人只能降级成 OCR 兼容模式（发图、粘贴、点输入框全不可靠）。
            # 所以启动前先单独热激活一次；微信没开时跳过，交给连接结果自己报错。
            $probePy = Join-Path $root '_probe_uia.py'
            $wxProcs = @(Get-Process Weixin, WeChat -ErrorAction SilentlyContinue)
            if ($wxProcs.Count -gt 0 -and (Test-Path $probePy)) {
                Write-Host '      热激活微信 UIA 无障碍树...'
                $probeOut = (& $vpy $probePy 2>&1 | Out-String)
                if ($probeOut -match 'materialized:\s*True') {
                    Write-Host '      UIA 树已就绪' -ForegroundColor Green
                } else {
                    Write-Host '      [提示] UIA 热激活未成功，机器人可能降级为 OCR 兼容模式' -ForegroundColor Yellow
                }
            }

            Write-Host '      后台启动 Web 控制台（独立进程，关掉本窗口也不受影响）...'
            Start-Process -FilePath $vpy -ArgumentList 'web_main.py' `
                -WorkingDirectory $root -WindowStyle Minimized
        }

        if (-not (Wait-Web 180)) {
            Write-Host '[错误] Web 控制台 180 秒内未就绪，请查看 logs\chatbot.log 与 logs\errors.log。' -ForegroundColor Red
            Pause-IfInteractive '按回车退出'
            exit 1
        }

        Write-Host '[2/2] 拉起机器人（连接微信 + 开始监听）...'
        $ok = Invoke-StartBot
        if (-not $ok) {
            Write-Host ''
            Write-Host '  [错误] 机器人没有启动成功。常见原因：' -ForegroundColor Red
            Write-Host '    · 微信没登录 / 主窗口被最小化（要保持可见）' -ForegroundColor Red
            Write-Host '    · 微信刚重启，UIA 树需要热激活（脚本已尝试，可手动跑 _probe_uia.py）' -ForegroundColor Red
            Write-Host '    · 上一次实例没退干净：先运行 停止-鲸鱼娘.bat 再启动' -ForegroundColor Red
            Show-Status
            Pause-IfInteractive '按回车退出'
            exit 1
        }

        Show-Status
        Write-Host ''
        Write-Host '  控制台地址（在这里填 API Key / 群名）: ' -NoNewline
        Write-Host $web -ForegroundColor Cyan
        Write-Host '  微信主窗口请保持可见，不要最小化，否则机器人发不出消息。' -ForegroundColor Yellow
        Write-Host ''
        Start-Process $web
        Pause-IfInteractive '按回车关闭本窗口（机器人继续在后台运行）'
    }
}
