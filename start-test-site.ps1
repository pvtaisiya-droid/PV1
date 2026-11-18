# Compatibility entry point: all user launches use the main installation.
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-site.ps1') -Port $Port -NoBrowser:$NoBrowser
