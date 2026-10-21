<#
.SYNOPSIS
    Harness² Quick Operations Helper
.DESCRIPTION
    Wrapper sederhana agar tidak perlu menghafal parameter CLI Harness².
.EXAMPLE
    .\harness.ps1                       # Menampilkan menu & panduan cepat
    .\harness.ps1 check dams            # Cek kesiapan DAMS
    .\harness.ps1 dry dams              # Dry-run DAMS
    .\harness.ps1 run dams              # Jalankan optimasi LLM DAMS
    .\harness.ps1 judge dams            # Grade hasil DAMS
    .\harness.ps1 report dams           # Tampilkan laporan DAMS
    .\harness.ps1 check bp              # Cek kesiapan Blueprint
    .\harness.ps1 dry bp                # Dry-run Blueprint
    .\harness.ps1 run bp                # Jalankan optimasi LLM Blueprint
#>

param (
    [Parameter(Position=0)]
    [ValidateSet("check", "dry", "run", "judge", "report", "setup", "help", "menu")]
    [string]$Action = "menu",

    [Parameter(Position=1)]
    [ValidateSet("dams", "bp", "jb", "lab", "wb")]
    [string]$Bench = "dams",

    [Parameter(Position=2)]
    [string]$Domain = "",

    [Parameter(Position=3)]
    [int]$K = 1,

    [Parameter(Position=4)]
    [string]$Mode = "parallel"
)

function Show-CheatSheet {
    Write-Host ""
    Write-Host "==========================================================================" -ForegroundColor Cyan
    Write-Host "          Harness2 Operational Cheat Sheet (Helper CLI)                   " -ForegroundColor Yellow
    Write-Host "==========================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Perintah Cepat (Menggunakan command 'harness' dari direktori mana saja):" -ForegroundColor Green
    Write-Host "  harness check  <dams|bp>        -> Cek kesiapan adapter"
    Write-Host "  harness dry    <dams|bp>        -> Preview urutan task (Zero Cost)"
    Write-Host "  harness run    <dams|bp>        -> Jalankan eksperimen nyata LLM"
    Write-Host "  harness judge  <dams|bp>        -> Penilaian otomatis deliverable"
    Write-Host "  harness report <dams|bp>        -> Tampilkan skor akhir & biaya USD"
    Write-Host ""
    Write-Host "Contoh Menjalankan 1 Domain Tertentu:" -ForegroundColor Green
    Write-Host "  harness run dams backend        -> Hanya domain backend DAMS"
    Write-Host "  harness run dams architecture   -> Hanya domain arsitektur DAMS"
    Write-Host "  harness run bp wi               -> Hanya domain WI Blueprint"
    Write-Host ""
    Write-Host "Perintah Asli Python (Jika tidak pakai script helper):" -ForegroundColor Magenta
    Write-Host "  python -m harness2.cli.check --bench <dams|bp>"
    Write-Host "  python -m harness2.cli.run_sweep --bench <dams|bp> --dry-run"
    Write-Host "  python -m harness2.cli.run_sweep --bench <dams|bp> --k 1 --mode parallel"
    Write-Host "  python -m harness2.cli.judge --bench <dams|bp> --k 1 --modes parallel"
    Write-Host "  python -m harness2.cli.report --bench <dams|bp> --k 1 --modes parallel"
    Write-Host "==========================================================================" -ForegroundColor Cyan
    Write-Host ""
}

if ($Action -eq "menu" -or $Action -eq "help") {
    Show-CheatSheet
    exit 0
}

$HarnessRoot = $PSScriptRoot
if (-not $HarnessRoot) {
    $HarnessRoot = "D:\Apps\harness2"
}

Push-Location $HarnessRoot
try {
    # Ensure harness2 is in PYTHONPATH
    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = "$HarnessRoot;$env:PYTHONPATH"
    } else {
        $env:PYTHONPATH = $HarnessRoot
    }

    Write-Host "Executing Harness2: Action='$Action' Bench='$Bench'..." -ForegroundColor Cyan

    switch ($Action) {
        "setup" {
            python -m harness2.cli.setup $Bench
        }
        "check" {
            python -m harness2.cli.check --bench $Bench
        }
        "dry" {
            if ($Domain) {
                python -m harness2.cli.run_sweep --bench $Bench --only-domains $Domain --k $K --mode $Mode --dry-run
            } else {
                python -m harness2.cli.run_sweep --bench $Bench --k $K --mode $Mode --dry-run
            }
        }
        "run" {
            if ($Domain) {
                Write-Host "Running sweep for benchmark '$Bench' on domain '$Domain'..." -ForegroundColor Yellow
                python -m harness2.cli.run_sweep --bench $Bench --only-domains $Domain --k $K --mode $Mode
            } else {
                Write-Host "Running sweep for benchmark '$Bench' across all domains..." -ForegroundColor Yellow
                python -m harness2.cli.run_sweep --bench $Bench --k $K --mode $Mode
            }
        }
        "judge" {
            python -m harness2.cli.judge --bench $Bench --k $K --modes $Mode
        }
        "report" {
            python -m harness2.cli.report --bench $Bench --k $K --modes $Mode
        }
    }
} finally {
    Pop-Location
}
