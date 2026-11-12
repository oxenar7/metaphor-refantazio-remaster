# Metaphor: ReFantazio Remaster - installer.
# Finds the game and Reloaded-II, downloads the data parts if they are missing, and rebuilds the mod's texture
# files from your own base.cpk. Nothing outside the new mod folder is changed.
param(
    [string]$GameDir = '',        # folder that contains base.cpk (auto-detected from Steam)
    [string]$ModsDir = '',        # Reloaded-II "Mods" folder (auto-detected from Reloaded-II's settings)
    [string]$DataDir = '',        # where the .bin data parts are / go (default: the "data" folder next to this script)
    [string[]]$Skip = @(),        # categories to leave out, e.g. -Skip character,enemy
    [switch]$All,                 # install everything without asking
    [switch]$NoDownload           # never download; only use parts that are already there
)
$ErrorActionPreference = 'Stop'
$ModId = 'oxenar7.metaphor.remaster'
$Skip = @($Skip | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Root = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) { $DataDir = Join-Path $Root 'data' }
$Manifest = Join-Path $DataDir 'manifest.txt.gz'

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Gb($b) { '{0:0.0} GB' -f ($b / 1e9) }
function Ask($q, $def = 'y') {
    if ($All) { return $true }
    $a = Read-Host "$q [$(if ($def -eq 'y') {'Y/n'} else {'y/N'})]"
    if (-not $a) { $a = $def }
    return $a.Trim().ToLower().StartsWith('y')
}

Say ''
Say '  Metaphor: ReFantazio Remaster - installer' 'Cyan'
Say '  ----------------------------------------' 'Cyan'
if (-not (Test-Path $Manifest)) { throw "manifest.txt.gz not found in $DataDir. Extract the whole zip first." }

Add-Type -Path (Join-Path $PSScriptRoot 'Builder.cs')
$m = [MetaphorRemaster.Installer]::Load($Manifest)
Say "  version $($m.Version), $($m.Files.Count) texture files"

if (Get-Process -Name METAPHOR -ErrorAction SilentlyContinue) { throw 'The game is running. Close it, then run the installer again.' }

# --- game folder ---
if (-not $GameDir) {
    $cands = @()
    foreach ($k in 'HKCU:\Software\Valve\Steam', 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam') {
        try { $sp = (Get-ItemProperty $k -ErrorAction Stop); foreach ($v in $sp.SteamPath, $sp.InstallPath) { if ($v) { $cands += $v } } } catch {}
    }
    $libs = @()
    foreach ($s in $cands | Select-Object -Unique) {
        $libs += $s
        $vdf = Join-Path $s 'steamapps\libraryfolders.vdf'
        if (Test-Path $vdf) { foreach ($mt in [regex]::Matches((Get-Content $vdf -Raw), '"path"\s+"([^"]+)"')) { $libs += $mt.Groups[1].Value -replace '\\\\', '\' } }
    }
    foreach ($l in $libs | Select-Object -Unique) {
        $g = Join-Path $l 'steamapps\common\METAPHOR'
        if (Test-Path (Join-Path $g 'base.cpk')) { $GameDir = $g; break }
    }
}
while (-not $GameDir -or -not (Test-Path (Join-Path $GameDir 'base.cpk'))) {
    $GameDir = (Read-Host '  Game folder not found. Paste the folder that contains base.cpk').Trim('"', ' ')
}
$Cpk = Join-Path $GameDir 'base.cpk'
Say "  game:      $GameDir"

# --- Reloaded-II mods folder ---
$RelCfg = Join-Path $env:APPDATA 'Reloaded-Mod-Loader-II\ReloadedII.json'
$UserCfgDir = $null
if (-not $ModsDir -and (Test-Path $RelCfg)) {
    $rc = Get-Content $RelCfg -Raw | ConvertFrom-Json
    $ModsDir = $rc.ModConfigDirectory; $UserCfgDir = $rc.ModUserConfigDirectory
}
while (-not $ModsDir -or -not (Test-Path $ModsDir)) {
    $ModsDir = (Read-Host '  Reloaded-II not found. Paste your Reloaded-II "Mods" folder').Trim('"', ' ')
}
Say "  Reloaded:  $ModsDir"
foreach ($dep in @(@('mrfpc.modloader', 'Metaphor Essentials'), @('crifs.v2.hook', 'CRI FileSystem V2 Hook'))) {
    if (-not (Test-Path (Join-Path $ModsDir $dep[0]))) { Say "  WARNING: $($dep[1]) ($($dep[0])) is not installed in Reloaded-II. The mod needs it." 'Yellow' }
}
if ($UserCfgDir) {
    $ecfg = Join-Path $UserCfgDir 'mrfpc.modloader\Config.json'
    $f4k = $false; if (Test-Path $ecfg) { try { $f4k = [bool](Get-Content $ecfg -Raw | ConvertFrom-Json).Force4k } catch {} }
    if (-not $f4k) { Say '  NOTE: turn on "Force 4K Assets" in Metaphor Essentials (Reloaded-II > Configure Mod), most textures are in the 4K set.' 'Yellow' }
}

# --- what to install ---
$sizes = $m.CategorySizes()
$charSize = 0; foreach ($c in 'character', 'enemy') { if ($sizes.ContainsKey($c)) { $charSize += $sizes[$c] } }
$total = 0; foreach ($v in $sizes.Values) { $total += $v }
if (-not $Skip -and -not $All) {
    Say ''
    Say "  1) Everything                                  ($(Gb $total) on disk)"
    Say "  2) Everything except characters and enemies     ($(Gb ($total - $charSize)))"
    $c = Read-Host '  Choose 1 or 2 [1]'
    if ($c -eq '2') { $Skip = @('character', 'enemy') }
}
$files = $m.Select([string[]]$Skip)
$parts = $m.PartsFor($files)
$outRoot = Join-Path $ModsDir "$ModId\MFEssentials\CPK\Remaster"

# --- data parts ---
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$missing = @($parts | Where-Object { -not (Test-Path (Join-Path $DataDir $_.Name)) -or (Get-Item (Join-Path $DataDir $_.Name)).Length -ne $_.Size })
$dl = 0; foreach ($p in $missing) { $dl += $p.Size }
$need = [MetaphorRemaster.Installer]::SumOut($files)
Say ''
Say "  to build:  $($files.Count) files, $(Gb $need)"
Say "  data:      $($parts.Count) parts, $($missing.Count) missing ($(Gb $dl) to download)"
$drvOut = (Get-Item $ModsDir).PSDrive; $drvData = (Get-Item $DataDir).PSDrive
if ($drvOut.Name -eq $drvData.Name) { if ($drvOut.Free -lt $need + $dl + 1e9) { Say "  WARNING: drive $($drvOut.Name): has $(Gb $drvOut.Free) free, about $(Gb ($need + $dl)) is needed." 'Yellow' } }
else {
    if ($drvOut.Free -lt $need + 1e9) { Say "  WARNING: drive $($drvOut.Name): has $(Gb $drvOut.Free) free, $(Gb $need) is needed." 'Yellow' }
    if ($drvData.Free -lt $dl + 1e8) { Say "  WARNING: drive $($drvData.Name): has $(Gb $drvData.Free) free, $(Gb $dl) is needed for the download." 'Yellow' }
}
if ($missing.Count -gt 0) {
    if ($NoDownload) { throw "Missing data parts in $DataDir. Download them from $($m.BaseUrl -replace '/download/', '/tag/')" }
    if (-not (Ask "  Download the missing parts from GitHub now?")) {
        Say "  Put the .bin parts in $DataDir and run the installer again." 'Yellow'; exit 1
    }
    $i = 0
    foreach ($p in $missing) { $i++; Say "  [$i/$($missing.Count)] downloading $($p.Name)"; $m.Download($p, $DataDir) }
}

# --- build ---
Say ''
Say "  Building into $ModsDir\$ModId ..." 'Cyan'
$r = $m.Build($Cpk, $DataDir, $outRoot, $files)
$cfgSrc = Join-Path $PSScriptRoot 'ModConfig.json'
$modDir = Join-Path $ModsDir $ModId
New-Item -ItemType Directory -Force -Path $modDir | Out-Null
[IO.File]::WriteAllText((Join-Path $modDir 'ModConfig.json'), (Get-Content $cfgSrc -Raw).Replace('@VERSION@', $m.Version), (New-Object Text.UTF8Encoding $false))
$icon = Join-Path $PSScriptRoot 'Preview.png'; if (Test-Path $icon) { Copy-Item $icon $modDir -Force }

Say ''
Say "  built $($r.Built), already up to date $($r.AlreadyThere), game file differs $($r.GameDiffers), failed $($r.Failed)"
foreach ($msg in $r.Messages | Select-Object -First 15) { Say "    $msg" }
if ($r.GameDiffers -gt 0) { Say "  $($r.GameDiffers) files were skipped because your game version differs from the one this release was made for. Those textures stay original." 'Yellow' }
if ($r.Failed -gt 0) {
    Say '  Some files failed their check. Checking the data parts...' 'Red'
    foreach ($p in $parts) {
        $f = Join-Path $DataDir $p.Name
        if ([MetaphorRemaster.Installer]::Sha256File($f, $p.Name) -ne $p.Sha256) { Remove-Item -LiteralPath $f; Say "    $($p.Name) was damaged and has been deleted." 'Red' }
    }
    Say '  Run the installer again: it downloads the deleted parts and finishes the remaining files.' 'Yellow'; exit 2
}
Say ''
Say '  Done. In Reloaded-II, tick "Metaphor: ReFantazio Remaster" for Metaphor and launch the game from there.' 'Green'
if ($r.Built -gt 0 -and -not $All) {
    if (Ask "  Delete the data parts ($(Gb (($parts | Measure-Object Size -Sum).Sum))) now? Keep them if you may reinstall later." 'n') {
        foreach ($p in $parts) { Remove-Item -LiteralPath (Join-Path $DataDir $p.Name) -ErrorAction SilentlyContinue }
    }
}
