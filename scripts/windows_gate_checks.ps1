# MD-184 part B: the scripted steps of the Gate 2 checklist (comment 11024 on MD-184), run INSIDE the Windows VM by scripts/windows_vm_gate.sh.
#
#   windows_gate_checks.ps1 -Phase before     steps 3b/4: background on/off, the login item, stop during a sync
#   windows_gate_checks.ps1 -Phase after      step 6: after a reboot (auto sign-in): the login item started the process, the next sync is clean
#
# Each check appends one JSON line to C:\gate\results.jsonl: {"phase","check","status":"PASS|FAIL","seconds","detail"}.
# It never uses a real token: the sources are plain folders. What only a person can judge (screens, a flashing console window, the text Windows
# shows for a startup app, "Open") is NOT here: the gate report lists those as "not covered".
param([Parameter(Mandatory)][ValidateSet('before', 'after')][string]$Phase)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$env:MDPIPELINE_NO_OPEN = '1'
$Root = 'C:\gate'
$Brain = "$Root\brain"
$Results = "$Root\results.jsonl"
$Vbs = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\mdpipeline-background.vbs"
$Py = "$env:APPDATA\uv\tools\mdpipeline\Scripts\python.exe"
$Big = 1500                                  # files in the slow pipeline: a sync that lasts several seconds

function Record([string]$Check, [bool]$Ok, [double]$Seconds, [string]$Detail) {
    $line = @{ phase = $Phase; check = $Check; status = $(if ($Ok) { 'PASS' } else { 'FAIL' }); seconds = [math]::Round($Seconds, 1); detail = $Detail } | ConvertTo-Json -Compress
    Add-Content -Path $Results -Value $line -Encoding utf8
    Write-Output ("{0} {1}: {2}" -f $(if ($Ok) { 'PASS' } else { 'FAIL' }), $Check, $Detail)
}

function Mdp { & mdpipeline --brain $Brain @args 2>&1 | ForEach-Object { "$_" } }       # stderr lines (usage notice) become plain text

function BgRunning {      # cheap on purpose: the app's own record (background.json: pid, version) and one Get-Process. WMI queries every half second starve the sync.
    try { $r = Get-Content "$env:LOCALAPPDATA\mdpipeline\background.json" -Raw -ErrorAction Stop | ConvertFrom-Json } catch { return $false }
    return [bool](Get-Process -Id $r.pid -ErrorAction SilentlyContinue)
}

function WaitFor([scriptblock]$Condition, [int]$Seconds) {      # returns the seconds it took, or $null on timeout
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $Seconds) {
        if (& $Condition) { return $clock.Elapsed.TotalSeconds }
        Start-Sleep -Seconds 1
    }
    return $null
}

function SyncWhenFree([string]$Name) {      # a sync another process is running answers "busy" (no summary): that is correct, so wait for it and ask again
    for ($i = 0; $i -lt 18; $i++) {
        $o = (Mdp sync $Name) -join "`n"
        if ($o -match 'created') { return $o }
        Start-Sleep -Seconds 10
    }
    return $o
}

function OwnedFileCount { $n = 0; foreach ($f in [IO.Directory]::EnumerateFiles($Brain, '*.md', 'AllDirectories')) { if ($f -match '\\raw\\') { $n++ } }; $n }

if ($Phase -eq 'before') {
    Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force "$Root\small", "$Root\big", $Brain | Out-Null
    1..20 | ForEach-Object { Set-Content "$Root\small\s$_.md" "# Small $_`nbody $_" }
    1..$Big | ForEach-Object { Set-Content "$Root\big\b$_.md" ("# Big $_`n" + ('line of text ' * 40)) }
    New-Item -ItemType Directory -Force 'C:\shots' | Out-Null          # text the gate shows in a real console window and photographs (the report's screenshots)
    Mdp init --create $Brain | Out-Null
    Mdp pipeline add small --source "small=$Root\small" --directory $Brain | Out-Null

    # A: a plain sync works and the app reports it
    $t = [Diagnostics.Stopwatch]::StartNew()
    $out = (Mdp sync small) -join "`n"
    Set-Content 'C:\shots\first-sync.txt' ("PS> mdpipeline sync small`n" + $out) -Encoding utf8
    Record 'first_sync' ($out -match 'errors 0' -and $out -match 'created 20') $t.Elapsed.TotalSeconds ($out -split "`n" | Where-Object { $_ -match 'created' } | Select-Object -First 1)

    # B: background on -> login item written, one process starts
    (Mdp schedule background off) | Out-Null
    [void](WaitFor { (-not (BgRunning)) } 60)
    $t = [Diagnostics.Stopwatch]::StartNew()
    (Mdp schedule background on) | Out-Null
    $s = WaitFor { (BgRunning) } 60
    Record 'background_on_starts_process' ($null -ne $s) $t.Elapsed.TotalSeconds $(if ($null -ne $s) { "running after $([math]::Round($s,1)) s" } else { 'no background process within 60 s' })
    Record 'background_on_writes_login_item' (Test-Path $Vbs) 0 $(if (Test-Path $Vbs) { $Vbs } else { "missing: $Vbs" })

    # C: stop during a sync. A pipeline is only scheduled after its first sync, so: sync "slow" once with 5 files, add $Big more, make it due
    #    (its last success moved 2 days back), start the process (it syncs at the first tick) and ask it to stop while the files are being written.
    New-Item -ItemType Directory -Force "$Root\slow" | Out-Null
    1..5 | ForEach-Object { Copy-Item "$Root\big\b$_.md" "$Root\slow\b$_.md" }
    Mdp pipeline add slow --source "slow=$Root\slow" --directory $Brain | Out-Null
    (Mdp sync slow) | Out-Null
    (Mdp schedule background off) | Out-Null
    [void](WaitFor { (-not (BgRunning)) } 60)
    Copy-Item "$Root\big\*.md" "$Root\slow\" -Force
    # "due" comes from the pipeline's last run in the activity log: move that run 2 days back
    & $Py -c "import json,datetime as D,pathlib; p=pathlib.Path(r'$Brain')/'.mdpipeline'/'log.jsonl'; out=[]
for l in p.read_text(encoding='utf-8').splitlines():
    if not l.strip(): continue
    e=json.loads(l)
    if e.get('type')=='run' and e.get('pipeline')=='slow': e['ts']=(D.datetime.fromisoformat(e['ts'].rstrip('Z'))-D.timedelta(days=2)).isoformat()+'Z'
    out.append(json.dumps(e))
p.write_text(chr(10).join(out)+chr(10),encoding='utf-8')"
    $before = OwnedFileCount
    (Mdp schedule background on) | Out-Null
    $started = WaitFor { (OwnedFileCount) -gt $before } 150
    $partial = OwnedFileCount
    $t = [Diagnostics.Stopwatch]::StartNew()
    (Mdp schedule background off) | Out-Null
    $mid = ($null -ne $started) -and ($partial - $before -lt $Big - 5)
    Record 'sync_was_running_when_stop_asked' $mid 0 "files written when stop was asked: $($partial - $before) of $($Big - 5)"
    $exited = WaitFor { (-not (BgRunning)) } 600
    Record 'stop_ends_the_process' ($null -ne $exited) $t.Elapsed.TotalSeconds $(if ($null -ne $exited) { "process gone $([math]::Round($exited,1)) s after the request" } else { 'still running 600 s after the request (the sync it was in should have finished and ended it)' })
    Record 'stop_removes_login_item' (-not (Test-Path $Vbs)) 0 $(if (Test-Path $Vbs) { 'login item still there' } else { 'removed' })

    # D: the sync it was in was not cut in half: every file is whole, and the next sync completes with no error and nothing to repair
    $total = OwnedFileCount
    Record 'stop_lets_the_sync_finish' ($total -ge $Big + 20) 0 "owned files after the process ended: $total (expected $($Big + 20))"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $out = SyncWhenFree 'slow'
    $summary = $out -split "`n" | Where-Object { $_ -match 'created' } | Select-Object -First 1
    Record 'next_sync_is_clean' ($out -match 'errors 0' -and $out -match 'conflicts 0') $t.Elapsed.TotalSeconds "$summary"
    $bad = 0
    Get-ChildItem -File "$Root\big" | ForEach-Object {
        $copy = Get-ChildItem -Recurse -File $Brain -Filter "*_$($_.Name)" | Where-Object { $_.FullName -match '\\raw\\' } | Select-Object -First 1
        if (-not $copy -or $copy.Length -lt $_.Length) { $bad++ }
    }
    Record 'files_intact_after_stop' ($bad -eq 0) 0 "files missing or shorter than their source: $bad of $Big"

    # E: leave background ON for the reboot check (step 6)
    (Mdp schedule background on) | Out-Null
}

if ($Phase -eq 'after') {
    # F: after the reboot the login item (Startup folder) starts the process by itself, with no command typed
    $t = [Diagnostics.Stopwatch]::StartNew()
    $s = WaitFor { (BgRunning) } 180
    Record 'reboot_login_item_starts_process' ($null -ne $s) $t.Elapsed.TotalSeconds $(if ($null -ne $s) { "running $([math]::Round($s,1)) s after the sign-in" } else { 'no background process 180 s after the reboot' })
    Record 'reboot_login_item_still_there' (Test-Path $Vbs) 0 $(if (Test-Path $Vbs) { 'present' } else { 'missing' })

    # G: a sync after the reboot: no vault / keychain error (MD-180 AC3). A file added now is picked up.
    Set-Content "$Root\small\after-reboot.md" "# After reboot`nnew file"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $out = SyncWhenFree 'small'
    Record 'reboot_next_sync_ok' ($out -match 'errors 0' -and $out -notmatch '(?i)vault is locked|keychain') $t.Elapsed.TotalSeconds ($out -split "`n" | Where-Object { $_ -match 'created|vault|keychain' } | Select-Object -First 1)
    $status = (Mdp status) -join "`n"
    Set-Content 'C:\shots\status-after-reboot.txt' ("PS> mdpipeline status`n" + $status) -Encoding utf8
    Record 'reboot_status_ok' ($status -match '\[ok\]') 0 ($status -split "`n" | Select-Object -First 1)
    (Mdp schedule background off) | Out-Null
}

New-Item -ItemType File -Force -Path "$Root\done-$Phase" | Out-Null          # the gate waits for this file (the checks run in the desktop session, not in the SSH session)
