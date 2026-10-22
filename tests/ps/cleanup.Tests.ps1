# Pester 5 tests for cleanup.ps1 on Windows PowerShell 5.1. Run: Invoke-Pester tests\ps
# They only read the script; the dry runs are tested from pytest (tests\test_cleanup_ps1.py).

BeforeAll {
    $script:CleanupPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'cleanup.ps1'
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

    It 'keeps every setting the README documents' {
        $text = Get-Content -LiteralPath $CleanupPath -Raw
        foreach ($name in 'CondaCache', 'PipCache', 'UvCache', 'GpuShaderCache', 'TempFiles', 'RecycleBin',
                          'CrashDumps', 'Hibernation', 'Installers', 'InstallerFolders', 'CondaEnvsToRemove',
                          'AppsToUninstall', 'ExtraPathsToDelete') {
            $text | Should -Match "(?m)^\`$$name\s*="
        }
    }
}
