<#
.SYNOPSIS
    Sauvegarde les volumes de donnees nommes de la pile fusionnee (Postgres x2,
    ArcadeDB) avant toute mise a jour d'image -- voir ROLLBACK.md.

.DESCRIPTION
    Un retour en arriere logiciel (rollback d'image) ne restaure PAS l'etat
    anterieur des donnees si la nouvelle version a modifie un schema ou un
    format de stockage entre-temps. Ce script prend un instantane brut de
    chaque volume nomme, via un conteneur "alpine" jetable qui le monte en
    lecture seule -- la meme methode fonctionne pour les trois volumes sans
    dependre de l'outillage propre a chaque base de donnees (pg_dump,
    export ArcadeDB, etc.).

.PARAMETER ProjectName
    Prefixe des volumes Docker (COMPOSE_PROJECT_NAME). Par defaut, lit la
    valeur de web-app-configuration/.env si presente, sinon retombe sur
    "web-app-configuration" (valeur par defaut de .env.example).

.EXAMPLE
    ./scripts/backup-volumes.ps1
    Sauvegarde les 3 volumes dans un nouveau dossier horodate sous backups/.
#>

param(
    [string]$ProjectName
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $ProjectName) {
    $envFile = Join-Path $RepoRoot ".env"
    $ProjectName = "web-app-configuration"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*COMPOSE_PROJECT_NAME\s*=" } | Select-Object -First 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim()
            if ($value) { $ProjectName = $value }
        }
    }
}

$Volumes = @("postgres_data", "postgres_app_data", "arcadedb_data")
$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$BackupDir = Join-Path $RepoRoot "backups\$Timestamp"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

Write-Host "Sauvegarde des volumes du projet '$ProjectName' vers $BackupDir" -ForegroundColor Cyan

foreach ($volume in $Volumes) {
    $fullVolumeName = "${ProjectName}_${volume}"

    $exists = docker volume inspect $fullVolumeName 2>$null
    if (-not $exists) {
        Write-Warning "Volume '$fullVolumeName' introuvable -- ignore (pile pas encore demarree ?)"
        continue
    }

    $archiveName = "$volume.tar.gz"
    Write-Host "  - $fullVolumeName -> $archiveName"

    # -v ...:/data:ro : montage en lecture seule, aucun risque d'ecrire dans le
    # volume source pendant la sauvegarde.
    docker run --rm `
        -v "${fullVolumeName}:/data:ro" `
        -v "${BackupDir}:/backup" `
        alpine sh -c "tar czf /backup/$archiveName -C /data ."

    if ($LASTEXITCODE -ne 0) {
        throw "Echec de la sauvegarde de '$fullVolumeName' (code $LASTEXITCODE)"
    }
}

Write-Host ""
Write-Host "Sauvegarde terminee :" -ForegroundColor Green
Get-ChildItem $BackupDir | ForEach-Object {
    $sizeMb = [math]::Round($_.Length / 1MB, 2)
    Write-Host "  $($_.Name)  ($sizeMb Mo)"
}
Write-Host ""
Write-Host "Pour restaurer un volume a partir de cette sauvegarde, voir ROLLBACK.md."
