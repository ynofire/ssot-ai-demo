# SSOT AI 프로토타입 실행 (Windows PowerShell)
#   실행:  .\start.ps1            (포트 변경: .\start.ps1 -Port 8010)
param([int]$Port = 8000)
Set-Location $PSScriptRoot

# 포트가 이미 사용 중이면 어떤 프로그램인지 보여주고, 끌지 물어본다
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    foreach ($c in $busy) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
        Write-Host "포트 $Port 을(를) 이미 쓰는 프로그램: PID $($c.OwningProcess) | $($p.Name) | $($p.CommandLine)"
    }
    $ans = Read-Host "이 프로그램을 종료하고 계속할까요? (y/N)"
    if ($ans -ne 'y') { Write-Host "중단했어요. 다른 포트로 실행하려면  .\start.ps1 -Port 8010"; exit 1 }
    $busy | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
    Start-Sleep 1
}

# Ollama / 모델 확인 (없어도 대시보드는 뜨지만 AI 채팅은 동작하지 않는다)
try {
    $tags = Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 2
    if (-not ($tags.models | Where-Object { $_.name -eq 'gemma4:e4b' })) { Write-Host "[주의] 모델이 없어요:  ollama pull gemma4:e4b" }
} catch { Write-Host "[주의] Ollama 가 실행 중이 아니에요. Ollama 를 켠 뒤 다시 실행하면 AI 채팅을 쓸 수 있어요." }

$env:PYTHONIOENCODING = 'utf-8'
python -m core.seed   # 데이터가 없으면 가상 데이터 생성 (업종: config\settings.json 또는 $env:VERTICAL)
Write-Host "서버를 시작합니다:  http://localhost:$Port   (끄기: Ctrl+C)"
Start-Job { Start-Sleep 2; Start-Process "http://localhost:$using:Port" } | Out-Null
python -m uvicorn core.main:app --port $Port
