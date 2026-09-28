# Quiz Study Helper 바탕화면 바로가기 생성 스크립트
# 사용: create_shortcut.bat 더블클릭 (관리자 권한 실행 바로가기: create_shortcut.bat admin)
param([switch]$Admin)

$ErrorActionPreference = 'Stop'
$AppDir  = $PSScriptRoot
$Pythonw = Join-Path $AppDir '.venv\Scripts\pythonw.exe'   # 콘솔 창 없이 실행
$MainPy  = Join-Path $AppDir 'main.py'
$Icon    = Join-Path $AppDir 'icon.ico'

if (-not (Test-Path $Pythonw)) {
    Write-Host '가상환경(.venv)을 찾을 수 없습니다.' -ForegroundColor Red
    Write-Host '먼저 README의 설치 단계를 진행해 주세요:'
    Write-Host '  python -m venv .venv'
    Write-Host '  .\.venv\Scripts\Activate.ps1'
    Write-Host '  pip install -r requirements.txt'
    exit 1
}
if (-not (Test-Path (Join-Path $AppDir '.env'))) {
    Write-Host '참고: .env 파일이 아직 없습니다. .env.example을 .env로 복사해 GEMINI_API_KEY를 입력해 주세요.' -ForegroundColor Yellow
}

try {
    # OneDrive로 옮겨진 바탕화면도 올바르게 찾는다
    $Desktop  = [Environment]::GetFolderPath('Desktop')
    $LinkPath = Join-Path $Desktop 'Quiz Study Helper.lnk'

    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut($LinkPath)
    $Link.TargetPath       = $Pythonw
    $Link.Arguments        = '"' + $MainPy + '"'
    $Link.WorkingDirectory = $AppDir
    $Link.IconLocation     = "$Icon,0"
    $Link.Description      = 'Quiz Study Helper - 객관식 문제 학습 도우미 (Ctrl+Shift+Q)'
    $Link.Save()

    if ($Admin) {
        # .lnk 헤더의 RunAsAdministrator 플래그(0x15 바이트의 0x20 비트)를 켠다
        $bytes = [System.IO.File]::ReadAllBytes($LinkPath)
        $bytes[0x15] = $bytes[0x15] -bor 0x20
        [System.IO.File]::WriteAllBytes($LinkPath, $bytes)
    }
} catch {
    Write-Host "바로가기를 만들지 못했습니다: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host '바탕화면에 바로가기를 만들었습니다:' -ForegroundColor Green
Write-Host "  $LinkPath"
if ($Admin) { Write-Host '  (관리자 권한으로 실행되도록 설정됨 - 실행할 때마다 확인 창이 뜹니다)' }
