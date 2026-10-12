[CmdletBinding(DefaultParameterSetName='Selected')]
param(
    [Parameter(Mandatory=$true)][string]$ExtractedRoot,
    [Parameter(Mandatory=$true,ParameterSetName='Selected')][string]$Model,
    [Parameter(Mandatory=$true,ParameterSetName='All')][switch]$AllModels,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$BlenderPath,
    [string]$Profile,
    [string]$HdriPath,
    [string]$LightingConfig,
    [switch]$Force,
    [switch]$ReuseCache,
    [switch]$ExportTextures,
    [switch]$KeepIntermediates,
    [switch]$BlendOnly,
    [ValidateSet('All','Highest','Lowest')][string]$ImportLOD = 'Highest',
    [ValidateSet('Veldin','Novalis','HDRI')][string]$LightingSource = 'HDRI',
    [switch]$NovalisAtmosphereSun,
    [switch]$FailOnMissingAssets,
    [switch]$ExperimentalAmbiguousInheritance,
    [string[]]$Clip
)
$ErrorActionPreference = 'Stop'
if (-not $BlenderPath) {
    $ps4Blenders = @(Get-ChildItem -LiteralPath (Join-Path $env:ProgramFiles 'Blender Foundation') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Blender [5-9]\.' } | Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'blender.exe' } | Where-Object { Test-Path -LiteralPath $_ })
    if ($ps4Blenders.Count -eq 0) { throw 'Blender 5.0+ was not found. Supply -BlenderPath with the full path to blender.exe.' }
    $BlenderPath = $ps4Blenders[0]
}
$BlenderPath = (Resolve-Path -LiteralPath $BlenderPath).Path
$ps4Python = @(Get-ChildItem -LiteralPath (Split-Path -Parent $BlenderPath) -Directory |
    ForEach-Object { Join-Path $_.FullName 'python\bin\python.exe' } | Where-Object { Test-Path -LiteralPath $_ })
if ($ps4Python.Count -ne 1) { throw 'Cannot find the Python runtime bundled with the selected Blender installation.' }
$ps4Arguments = @('-B', (Join-Path $PSScriptRoot '..\rc2016\pipeline\run.py'), '--extracted-root', $ExtractedRoot,
    '--output-root', $OutputRoot, '--blender', $BlenderPath)
if ($HdriPath) { $ps4Arguments += @('--hdri', $HdriPath) }
if ($LightingConfig) { $ps4Arguments += @('--lighting-config', $LightingConfig) }
if ($AllModels) { $ps4Arguments += '--all-models' } else { $ps4Arguments += @('--model', $Model) }
if ($Profile) { $ps4Arguments += @('--profile', $Profile) }
if ($Force) { $ps4Arguments += '--force' }
if ($ReuseCache) { $ps4Arguments += '--reuse-cache' }
if ($ExportTextures) { $ps4Arguments += '--export-textures' }
if ($KeepIntermediates) { $ps4Arguments += '--keep-intermediates' }
if ($BlendOnly) { $ps4Arguments += '--blend-only' }
$ps4Arguments += @('--import-lod', $ImportLOD)
$ps4Arguments += @('--lighting-source', $LightingSource.ToLowerInvariant())
if ($NovalisAtmosphereSun) { $ps4Arguments += '--novalis-atmosphere-sun' }
if ($FailOnMissingAssets) { $ps4Arguments += '--fail-on-missing-assets' }
if ($ExperimentalAmbiguousInheritance) { $ps4Arguments += '--experimental-ambiguous-inheritance' }
foreach ($clipSelector in $Clip) { $ps4Arguments += @('--clip', $clipSelector) }
& $ps4Python[0] @ps4Arguments
if ($LASTEXITCODE -ne 0) { throw "Pipeline failed (exit $LASTEXITCODE). Inspect reports and logs under $OutputRoot." }
