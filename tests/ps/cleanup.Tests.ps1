# Pester 5 tests for cleanup.ps1 on Windows PowerShell 5.1. Run: Invoke-Pester tests\ps
# They only read the script; the dry runs are tested from pytest (tests\test_cleanup_ps1.py).

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
        $text | Should -Match 'pc_tidy\.py" cleanup-settings'
    }

    It 'reads every key of the [cleanup] section in settings.template.ini' {
        $text = Get-Content -LiteralPath $CleanupPath -Raw
        $template = Get-Content -LiteralPath (Join-Path $Root 'settings.template.ini')
        $start = [array]::IndexOf($template, '[cleanup]')
        $keys = foreach ($line in $template[($start + 1)..($template.Count - 1)]) {
            if ($line -match '^\[') { break }
            if ($line -match '^(\w+)\s*=') { $Matches[1] }
        }
        $keys.Count | Should -BeGreaterThan 10
        foreach ($key in $keys) {
            $text | Should -Match "\`$s\.$key\b"
        }
    }
}
