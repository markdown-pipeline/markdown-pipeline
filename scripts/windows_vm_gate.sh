#!/bin/bash
# MD-184 part B: the Windows 11 Gate 2 VM on this Linux host (QEMU/KVM through libvirt, no sudo, no systemd).
#
#   scripts/windows_vm_gate.sh build          one time (about 30 min, no person): unattended install from the Windows 11 Enterprise EVALUATION ISO
#                                             -> a golden disk + UEFI variables. Prints the ISO sha256 and the evaluation expiry date (AC-B3).
#   scripts/windows_vm_gate.sh up             a fresh machine from the golden disk (a throwaway copy-on-write overlay), booted, SSH answering
#   scripts/windows_vm_gate.sh ssh [-R P:H:P] [COMMAND]   run a PowerShell command in it (no command: an interactive shell); -R reaches a host server
#   scripts/windows_vm_gate.sh copy SRC... DEST           copy files into it;   screenshot [FILE]   the screen as a PNG (evidence)
#   scripts/windows_vm_gate.sh down           power it off and delete the overlay (the golden disk is never written)
#   scripts/windows_vm_gate.sh status         expiry date and what exists
#   scripts/windows_vm_gate.sh check [WHEEL]  AC-B1/AC-B2: one command from clean to a pass/fail report. A fresh machine, the CANDIDATE build installed (a wheel
#                                             of this tree, or WHEEL), the scripted checklist steps run in the desktop session (scripts/windows_gate_checks.ps1),
#                                             a reboot, the checks after it; saved under $WINVM_STATE/reports/<time>/ (report.md, results.jsonl, screenshots).
#                                             Exit 0 only if every scripted check passed. What cannot be scripted is listed in the report as 'not covered'; the owner reviews report.md (DS-P-1102-2). No owner step is inside the run.
#
# "Revert to clean" is `down` then `up`: seconds, because every run starts from a new overlay of the golden disk.
# AC-B4: no secret is written into the image. The only credentials are a throwaway local account password and an SSH key made here, kept in
# $WINVM_STATE (mode 600, outside the repo). A Jira or GitHub token is passed at run time, over SSH, never stored in the golden disk.
set -euo pipefail

STORAGE="${WINVM_STORAGE:-/mnt/chroot-vms/qemu}"
STATE="${WINVM_STATE:-$HOME/.local/state/mdpipeline-winvm}"
ISO="${WINVM_ISO:-$STORAGE/iso/26300.9457.260913-1737.26h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso}"
ISO_SHA256="bc3f24086ebadc94489066b5ad78089e2cf5c3491e90e790bb81a2b199c10e38"      # recorded by product, comment 10819 on MD-184
GOLDEN="$STORAGE/images/win11-golden.qcow2"
GOLDEN_VARS="$STORAGE/nvram/win11-golden_VARS.fd"
OVERLAY="$STORAGE/images/win11-run.qcow2"
RUN_VARS="$STORAGE/nvram/win11-run_VARS.fd"
APPDIR="$(cd "$(dirname "$0")/.." && pwd)"
URI="qemu:///system"
V=(virsh -c "$URI")
OVMF_CODE="/usr/share/OVMF/x64/OVMF_CODE.4m.fd"
OVMF_VARS="/usr/share/OVMF/x64/OVMF_VARS.4m.fd"
BUILD_NAME="mdp-win11-build"
RUN_NAME="mdp-win11-run"
EVAL_DAYS=90
SSH_OPTS=(-i "$STATE/id_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes)

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

ip_of() {   # the VM's address on libvirt's default NAT network
  "${V[@]}" domifaddr "$1" --source lease 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
}

wait_ssh() {   # wait_ssh NAME TIMEOUT_SECONDS -> prints the IP
  local name="$1" deadline=$((SECONDS + $2)) ip=""
  while [ "$SECONDS" -lt "$deadline" ]; do
    ip="$(ip_of "$name" || true)"
    if [ -n "$ip" ] && ssh "${SSH_OPTS[@]}" "tester@$ip" 'echo ok' >/dev/null 2>&1; then echo "$ip"; return 0; fi
    sleep 10
  done
  return 1
}

expiry_note() {   # AC-B3: the evaluation shuts down hourly once expired; warn 14 days ahead
  [ -f "$STATE/built-at" ] || { echo "no golden image yet (run: build)"; return 0; }
  local built end left
  built="$(cat "$STATE/built-at")"; end=$(( built + EVAL_DAYS * 86400 )); left=$(( (end - $(date +%s)) / 86400 ))
  echo "Windows evaluation expires $(date -d @"$end" +%F) ($left days left)"
  if [ "$left" -le 14 ]; then echo "WARNING: the evaluation expires within 14 days: run '$0 build' again after it expires or before the next gate." >&2; fi
}

check_host() {
  [ -r /dev/kvm ] && [ -w /dev/kvm ] || die "/dev/kvm is not usable by $USER (are you in the kvm group, and logged in again?)"
  "${V[@]}" list --all >/dev/null 2>&1 || die "cannot talk to libvirt ($URI): are you in the libvirt group?"
  for f in "$ISO" "$OVMF_CODE" "$OVMF_VARS"; do [ -r "$f" ] || die "missing $f"; done
  command -v mkisofs >/dev/null || die "mkisofs is needed to make the answer-file disc"
  command -v sshpass >/dev/null >/dev/null 2>&1 || true
}

cmd_build() {
  check_host
  mkdir -p "$STATE" && chmod 700 "$STATE"
  log "ISO sha256 (must match product's record): $(sha256sum "$ISO" | cut -d' ' -f1)"
  [ "$(sha256sum "$ISO" | cut -d' ' -f1)" = "$ISO_SHA256" ] || die "the ISO is not the one product recorded ($ISO_SHA256)"
  [ -f "$STATE/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C "mdpipeline-winvm" -f "$STATE/id_ed25519"
  [ -f "$STATE/admin-password" ] || { umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20 > "$STATE/admin-password"; }
  local work; work="$(mktemp -d)"; trap 'rm -rf "$work"' RETURN
  mkdir "$work/disc"
  sed -e "s|@ADMIN_PASSWORD@|$(cat "$STATE/admin-password")|g" \
      "$APPDIR/scripts/windows_vm/Autounattend.xml.tmpl" > "$work/disc/Autounattend.xml"
  sed -e "s|@SSH_PUBLIC_KEY@|$(cat "$STATE/id_ed25519.pub")|g" "$APPDIR/scripts/windows_vm/setup-ssh.ps1.tmpl" > "$work/disc/setup-ssh.ps1"
  mkisofs -quiet -J -r -V UNATTEND -o "$work/unattend.iso" "$work/disc"
  "${V[@]}" destroy "$BUILD_NAME" >/dev/null 2>&1 || true; "${V[@]}" undefine "$BUILD_NAME" --nvram >/dev/null 2>&1 || true
  rm -f "$GOLDEN" "$GOLDEN_VARS"
  qemu-img create -q -f qcow2 "$GOLDEN" 64G
  cp "$OVMF_VARS" "$GOLDEN_VARS"
  rm -f "$STORAGE/iso/mdp-unattend.iso"          # a leftover from an earlier build is owned by libvirt (the directory is group-writable, the file is not)
  cp "$work/unattend.iso" "$STORAGE/iso/mdp-unattend.iso"
  log "starting the unattended install (about 30 minutes, nobody needs to click)"
  virt-install --connect "$URI" --name "$BUILD_NAME" --memory 8192 --vcpus 4 --cpu host-passthrough --osinfo win11 \
    --boot "loader=$OVMF_CODE,loader.readonly=yes,loader.type=pflash,nvram=$GOLDEN_VARS" \
    --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
    --disk "path=$GOLDEN,format=qcow2,bus=sata" --disk "path=$ISO,device=cdrom,bus=sata" --disk "path=$STORAGE/iso/mdp-unattend.iso,device=cdrom,bus=sata" \
    --network network=default,model=e1000e --graphics vnc,listen=127.0.0.1 --video vga --noautoconsole --check all=off
  # the Windows disc asks "press any key to boot from CD" for a few seconds when the disk is empty: answer it
  for _ in $(seq 1 25); do "${V[@]}" send-key "$BUILD_NAME" KEY_ENTER >/dev/null 2>&1 || true; sleep 1; done
  log "waiting for Windows to finish installing and for SSH to answer ..."
  local ip; ip="$(wait_ssh "$BUILD_NAME" 4500)" || { "${V[@]}" screenshot "$BUILD_NAME" "$STATE/last-failure.png" >/dev/null 2>&1 || true; die "Windows did not come up with SSH within 75 minutes (screen saved as $STATE/last-failure.png)"; }
  log "SSH is up at $ip; waiting for the first-logon setup to finish"
  for _ in $(seq 1 60); do ssh "${SSH_OPTS[@]}" "tester@$ip" 'if (Test-Path C:\build-done.txt) { "done" }' 2>/dev/null | grep -q done && break; sleep 10; done
  ssh "${SSH_OPTS[@]}" "tester@$ip" 'if (-not (Test-Path C:\build-done.txt)) { exit 1 }' || die "the first-logon setup did not finish"
  ssh "${SSH_OPTS[@]}" "tester@$ip" '[Environment]::OSVersion.VersionString; (Get-CimInstance Win32_OperatingSystem).Caption' || true
  log "shutting down and keeping the disk as the golden image"
  ssh "${SSH_OPTS[@]}" "tester@$ip" 'shutdown /s /t 5' >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do [ "$("${V[@]}" domstate "$BUILD_NAME" 2>/dev/null)" = "shut off" ] && break; sleep 5; done
  cp "$GOLDEN_VARS" "$STATE/golden_VARS.fd"          # `undefine --nvram` deletes the variables file: keep the Windows boot entries in a copy
  "${V[@]}" undefine "$BUILD_NAME" --nvram >/dev/null 2>&1 || "${V[@]}" undefine "$BUILD_NAME" >/dev/null 2>&1 || true
  cp "$STATE/golden_VARS.fd" "$GOLDEN_VARS"
  rm -f "$STORAGE/iso/mdp-unattend.iso"
  date +%s > "$STATE/built-at"
  chmod a-w "$GOLDEN" 2>/dev/null || true
  log "golden image ready: $GOLDEN"
  expiry_note
}

cmd_down() {
  "${V[@]}" destroy "$RUN_NAME" >/dev/null 2>&1 || true
  "${V[@]}" undefine "$RUN_NAME" --nvram >/dev/null 2>&1 || "${V[@]}" undefine "$RUN_NAME" >/dev/null 2>&1 || true
  rm -f "$OVERLAY" "$RUN_VARS" "$STATE/run-ip"
  log "the run machine is gone (the golden disk was not touched)"
}

cmd_up() {   # a fresh machine: a copy-on-write overlay of the golden disk, so "clean" costs seconds, not a reinstall
  check_host
  [ -f "$GOLDEN" ] || die "no golden image yet: run '$0 build' first"
  expiry_note
  cmd_down >/dev/null
  qemu-img create -q -f qcow2 -b "$GOLDEN" -F qcow2 "$OVERLAY"
  cp "$GOLDEN_VARS" "$RUN_VARS"
  local started=$SECONDS
  virt-install --connect "$URI" --name "$RUN_NAME" --memory 8192 --vcpus 4 --cpu host-passthrough --osinfo win11 --import \
    --boot "loader=$OVMF_CODE,loader.readonly=yes,loader.type=pflash,nvram=$RUN_VARS" \
    --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
    --disk "path=$OVERLAY,format=qcow2,bus=sata" --network network=default,model=e1000e --graphics vnc,listen=127.0.0.1 --video vga --noautoconsole --check all=off >/dev/null
  local ip; ip="$(wait_ssh "$RUN_NAME" 600)" || die "the run machine did not answer on SSH within 10 minutes"
  echo "$ip" > "$STATE/run-ip"
  log "up in $((SECONDS - started)) s at $ip (a clean Windows 11; ssh/copy/down are the next commands)"
}

run_ip() { [ -s "$STATE/run-ip" ] && cat "$STATE/run-ip" || die "no run machine: run '$0 up'"; }

cmd_ssh() {   # ssh [-R PORT:HOST:PORT ...] [COMMAND]: a PowerShell command in the run machine; -R reaches a server on this host without opening a listener to the network
  local fwd=()
  while [ "${1:-}" = "-R" ]; do fwd+=(-R "$2"); shift 2; done
  ssh "${SSH_OPTS[@]}" "${fwd[@]}" "tester@$(run_ip)" "$@"
}

cmd_copy() {  # copy SRC... DEST (DEST is a Windows path in the run machine, e.g. C:/Users/tester/)
  local dest="${*: -1}"; local srcs=("${@:1:$#-1}")
  scp "${SSH_OPTS[@]}" -r "${srcs[@]}" "tester@$(run_ip):$dest"
}

cmd_screenshot() { "${V[@]}" screenshot "$RUN_NAME" "${1:-$STATE/screen.png}" >/dev/null && echo "${1:-$STATE/screen.png}"; }

cmd_status() { expiry_note; ls -lh "$GOLDEN" 2>/dev/null || true; "${V[@]}" list --all | sed -n 1,6p; }

# ---- check: the scripted part of the Gate 2 checklist (comment 11024 on MD-184) ------------------------------------------------------------------
# Why the checks run in the DESKTOP session through a scheduled task and not in the SSH session: Windows ends every process of an SSH session when it
# closes, so a background process started there would die with it. A real user's process lives in their own sign-in session.
run_in_desktop() {   # run_in_desktop PHASE TIMEOUT_SECONDS: runs windows_gate_checks.ps1 -Phase PHASE in the signed-in session and waits for its marker file
  local phase="$1" limit="$2" tmp; tmp="$(mktemp -d)"
  printf 'CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\Users\\tester\\windows_gate_checks.ps1 -Phase %s", 0, True\r\n' "$phase" > "$tmp/run_gate.vbs"
  cat > "$tmp/start_task.ps1" <<'PS'
schtasks /end /tn mdpgate 2>&1 | Out-Null
schtasks /create /tn mdpgate /tr "wscript.exe C:\Users\tester\run_gate.vbs" /sc once /st 23:59 /it /f 2>&1 | Out-Null
schtasks /run /tn mdpgate 2>&1 | Out-Null
PS
  cmd_copy "$tmp/run_gate.vbs" "$tmp/start_task.ps1" C:/Users/tester/ >/dev/null
  cmd_ssh 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\tester\start_task.ps1' >/dev/null 2>&1
  local deadline=$((SECONDS + limit))
  while [ "$SECONDS" -lt "$deadline" ]; do
    local marker; marker="$(cmd_ssh "powershell -NoProfile -Command \"if (Test-Path C:\\gate\\done-$phase) { 'done' }\"" 2>/dev/null || true)"
    if grep -q done <<< "$marker"; then rm -rf "$tmp"; return 0; fi
    sleep 15
  done
  rm -rf "$tmp"; return 1
}

wait_desktop() {   # after a reboot: the auto sign-in has made an active console session
  local deadline=$((SECONDS + 300))
  while [ "$SECONDS" -lt "$deadline" ]; do
    local who; who="$(cmd_ssh 'query user' 2>/dev/null || true)"      # (not `| grep -q`: with pipefail the early exit makes the pipeline fail)
    if grep -qi 'console.*Active' <<< "$who"; then return 0; fi
    sleep 10
  done
  return 1
}

put_shot_text() {   # put_shot_text NAME LOCALFILE: the text a screenshot will show, as C:\shots\NAME.txt in the machine
  local tmp; tmp="$(mktemp -d)"
  printf 'New-Item -ItemType Directory -Force C:\\shots | Out-Null\r\n' > "$tmp/mkshots.ps1"
  cmd_copy "$tmp/mkshots.ps1" C:/Users/tester/ >/dev/null 2>&1 || true
  cmd_ssh 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\tester\mkshots.ps1' >/dev/null 2>&1 || true
  cp "$2" "$tmp/$1.txt"; cmd_copy "$tmp/$1.txt" C:/shots/ >/dev/null 2>&1 || true
  rm -rf "$tmp"
}

show_shot() {   # show_shot NAME: shows C:\shots\NAME.txt in a real console window (conhost, not Windows Terminal: it drew nothing in a scheduled task) on the desktop; saves $OUT/shot-NAME.png
  local name="$1" tmp; tmp="$(mktemp -d)"
  cat > "$tmp/start_show.ps1" <<PS
Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { \$_.MainWindowTitle -ne '' } | Stop-Process -Force -ErrorAction SilentlyContinue
schtasks /end /tn mdpshow 2>&1 | Out-Null
schtasks /create /tn mdpshow /tr "conhost.exe powershell.exe -NoProfile -NoExit -Command Get-Content C:\\shots\\$name.txt" /sc once /st 23:59 /it /f 2>&1 | Out-Null
schtasks /run /tn mdpshow 2>&1 | Out-Null
PS
  cmd_copy "$tmp/start_show.ps1" C:/Users/tester/ >/dev/null 2>&1 || true
  cmd_ssh 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\tester\start_show.ps1' >/dev/null 2>&1 || true
  # MD-184: wait until the console window really exists (right after the install the machine is busy and a fixed 8 s showed only the desktop), then let it paint
  local waited=0
  until cmd_ssh 'powershell -NoProfile -Command "if (Get-Process powershell | Where-Object { $_.MainWindowTitle -ne \"\" }) { exit 0 } else { exit 1 }"' >/dev/null 2>&1 || [ "$waited" -ge 90 ]; do sleep 5; waited=$((waited + 5)); done
  sleep 4
  cmd_screenshot "$OUT/shot-$name.png" >/dev/null 2>&1 || true
  cmd_ssh 'powershell -NoProfile -Command "Get-Process powershell | Where-Object { $_.MainWindowTitle -ne \"\" } | Stop-Process -Force"' >/dev/null 2>&1 || true
  rm -rf "$tmp"
}

cmd_check() {
  local wheel="${1:-}" out stamp tmp windows_build="unknown"; OUT=""
  stamp="$(date +%Y%m%d-%H%M%S)"; out="$STATE/reports/$stamp"; mkdir -p "$out"; OUT="$out"; tmp="$(mktemp -d)"
  local wdir="$tmp/dist"
  if [ -z "$wheel" ]; then
    (cd "$APPDIR" && uv build --wheel -o "$wdir" >/dev/null 2>&1) || die "could not build a wheel of this tree"
    wheel="$(ls "$wdir"/*.whl | head -1)"
  fi
  local wheel_sha version; wheel_sha="$(sha256sum "$wheel" | cut -d' ' -f1)"; version="$(basename "$wheel" | cut -d- -f2)"
  local commit; commit="$(git -C "$APPDIR" rev-parse --short HEAD 2>/dev/null || echo unknown)$(git -C "$APPDIR" diff --quiet 2>/dev/null || echo '+uncommitted')"
  log "check: candidate $(basename "$wheel") (sha256 ${wheel_sha:0:12}…) from commit $commit"
  cmd_up
  expiry_note | tee "$out/expiry.txt"

  # the candidate build, installed the way a user's machine would (uv from astral, then the wheel); nothing else is on the machine
  cat > "$tmp/install.ps1" <<'PS'
$ErrorActionPreference = 'Continue'
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Select-Object -Last 2
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$wheel = Get-ChildItem C:\Users\tester\mdpipeline-*.whl | Select-Object -First 1
uv tool install --force --reinstall --python 3.12 $wheel.FullName 2>&1 | Select-Object -Last 2
PS
  wait_desktop || die "no signed-in desktop session in the machine (auto sign-in did not happen)"
  local tries=0 installed=""
  while [ "$tries" -lt 4 ] && ! grep -q "Installed 1 executable" <<< "$installed"; do      # right after the first boot the machine is busy: SSH may time out
    tries=$((tries + 1))
    log "installing uv and the candidate wheel in the machine (try $tries)"
    cmd_copy "$wheel" "$tmp/install.ps1" "$APPDIR/scripts/windows_gate_checks.ps1" C:/Users/tester/ >/dev/null 2>&1 || { sleep 20; continue; }
    installed="$(cmd_ssh 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\tester\install.ps1' 2>&1 | tail -4 || true)"
    echo "$installed" | tail -2
  done
  local install_ok=PASS; grep -q "Installed 1 executable" <<< "$installed" || install_ok=FAIL
  printf '{"phase":"before","check":"install","status":"%s","seconds":0,"detail":"%s"}\n' "$install_ok" "$(tail -1 <<< "$installed" | tr -d '"\\\r')" > "$out/host-results.jsonl"
  [ "$install_ok" = PASS ] || { cp "$out/host-results.jsonl" "$out/results.jsonl"; python3 "$APPDIR/scripts/windows_gate_report.py" "$out" "$stamp" "$commit" "$(basename "$wheel")" "$wheel_sha" "$ISO_SHA256" "$(expiry_note 2>&1 | head -1)" "unknown (not installed)" || true; die "the candidate build could not be installed in the machine"; }
  windows_build="$(cmd_ssh 'cmd /c ver' 2>/dev/null | tr -d '\r' | grep -i version | head -1)"
  printf '%s\n' "$installed" > "$tmp/install.txt"; put_shot_text install "$tmp/install.txt"; cp "$tmp/install.txt" "$out/install-output.txt"
  show_shot install

  log "phase before (in the desktop session): sync, background on/off, login item, stop during a sync"
  run_in_desktop before 1200 || { cmd_screenshot "$out/before-timeout.png" >/dev/null; log "the 'before' phase did not finish in 20 minutes (screenshot saved)"; }
  show_shot first-sync
  cmd_ssh 'type C:\gate\results.jsonl' > "$out/results-before.jsonl" 2>/dev/null || true

  log "reboot: the auto sign-in must start the background process by itself"
  cmd_ssh 'shutdown /r /t 0' >/dev/null 2>&1 || true
  sleep 40
  wait_ssh "$RUN_NAME" 600 >/dev/null || die "the machine did not come back after the reboot"
  wait_desktop || die "no desktop session after the reboot"
  log "phase after"
  cmd_ssh 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\tester\windows_gate_checks.ps1 -Phase after' > "$out/after-output.txt" 2>&1 || true
  cmd_ssh 'type C:\gate\results.jsonl' > "$out/results.jsonl" 2>/dev/null || true

  show_shot status-after-reboot
  cat "$out/host-results.jsonl" "$out/results.jsonl" > "$tmp/all.jsonl" 2>/dev/null || true; cp "$tmp/all.jsonl" "$out/results.jsonl"
  local code=0
  python3 "$APPDIR/scripts/windows_gate_report.py" "$out" "$stamp" "$commit" "$(basename "$wheel")" "$wheel_sha" "$ISO_SHA256" "$(expiry_note 2>&1 | head -1)" "$windows_build" || code=$?
  rm -rf "$tmp"
  log "report saved in $out"
  return $code
}

case "${1:-}" in
  build) cmd_build ;;
  up) cmd_up ;;
  ssh) shift; cmd_ssh "$@" ;;
  copy) shift; cmd_copy "$@" ;;
  screenshot) shift; cmd_screenshot "$@" ;;
  down) cmd_down ;;
  status) cmd_status ;;
  check) shift; cmd_check "$@" ;;
  show) shift; OUT="$(dirname "${3:-./x}")"; put_shot_text "$1" "$2"; show_shot "$1"; echo "$OUT/shot-$1.png" ;;      # show NAME TEXTFILE [DIR/x]: one screen as a PNG
  *) sed -n 2,15p "$0"; exit 2 ;;
esac
