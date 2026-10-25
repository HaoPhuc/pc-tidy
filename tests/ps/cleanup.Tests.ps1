# Pester 5 tests for cleanup.ps1 on Windows PowerShell 5.1. Run: Invoke-Pester tests\ps
# They only read the script; its runs are tested from pytest (tests\test_cleanup_ps1.py).

BeforeAll {
    $script:Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:CleanupPath = Join-Path $Root 'cleanup.ps1'
}

Describe 'cleanup.ps1' {
    It 'runs on Windows PowerShell 5.1, the shell users have' {
        $PSVersionTable.PSEdition | Should -Be 'Desktop'
        $PSVersionTable.PSVersion.Major | Should -Be 5
    }

    It 'parses without errors' {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($CleanupPath, [ref]$tokens, [ref]$errors)
        $errors | Should -BeNullOrEmpty
    }

    It 'keeps its -DryRun switch' {
        $params = (Get-Command $CleanupPath).Parameters
        $params.Keys | Should -Contain 'DryRun'
        $params['DryRun'].SwitchParameter | Should -BeTrue
    }

    It 'has no SETTINGS block left: what it cleans comes from settings.ini' {
        $text = Get-Content -LiteralPath $CleanupPath -Raw
        $text | Should -Not -Match '(?m)^\$\w+\s*=\s*\$(true|false)\b'
    }

    It 'runs python pc_tidy.py cleanup, and passes -DryRun on as --dry-run' {
        $text = Get-Content -LiteralPath $CleanupPath -Raw
        $text | Should -Match 'pc_tidy\.py", "cleanup"'
        $text | Should -Match 'if \(\$DryRun\) \{ \$pyArgs \+= "--dry-run" \}'
        $text | Should -Match 'exit \$LASTEXITCODE'
    }

    It 'deletes nothing itself: the executor does, from the list you saw' {
        $text = Get-Content -LiteralPath $CleanupPath -Raw
        foreach ($command in 'Remove-Item', 'Clear-RecycleBin', 'powercfg', 'winget', 'conda clean', 'conda remove') {
            $text | Should -Not -Match ([regex]::Escape($command))
        }
    }
}

Describe 'setup.ps1' {
    BeforeAll {
        $script:SetupPath = Join-Path $Root 'setup.ps1'
    }

    It 'parses without errors' {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($SetupPath, [ref]$tokens, [ref]$errors)
        $errors | Should -BeNullOrEmpty
    }

    It 'has a -NonInteractive switch for CI' {
        (Get-Command $SetupPath).Parameters['NonInteractive'].SwitchParameter | Should -BeTrue
    }
}

Describe 'cleanup.ps1 --help' {
    It 'prints its help and exits 0' {
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $CleanupPath --help
        $LASTEXITCODE | Should -Be 0
        ($out -join "`n") | Should -Match 'cleanup\.ps1 -DryRun'
    }
}
