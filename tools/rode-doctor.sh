#!/usr/bin/env bash
# rode-doctor.sh — report the state of RØDE devices on this Linux system.
#
# Read-only by default: it inspects sysfs, /dev, ALSA and PipeWire and prints a
# report.  Nothing is modified.  With --record it additionally captures a few
# seconds from the receiver to tell audio and timecode channels apart.
#
# Paste the output into an issue to help extend docs/device-map.md.
#
# SPDX-License-Identifier: MIT

set -uo pipefail

VENDOR=19f7
RECORD=0
SECONDS_TO_RECORD=4

for arg in "$@"; do
  case "$arg" in
    --record) RECORD=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

bold()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
info()  { printf '    %s\n' "$1"; }

have() { command -v "$1" >/dev/null 2>&1; }

# ----------------------------------------------------------------- environment
bold "System"
info "$(uname -sr)"
[ -r /etc/os-release ] && info "$(. /etc/os-release; echo "$PRETTY_NAME")"
info "desktop: ${XDG_CURRENT_DESKTOP:-?} / ${XDG_SESSION_TYPE:-?}"

# --------------------------------------------------------------- usb inventory
bold "RØDE USB devices (vendor $VENDOR)"
found=0
for d in /sys/bus/usb/devices/*; do
  [ -r "$d/idVendor" ] || continue
  [ "$(cat "$d/idVendor")" = "$VENDOR" ] || continue
  found=$((found+1))
  pid=$(cat "$d/idProduct" 2>/dev/null)
  name=$(cat "$d/product" 2>/dev/null || echo "?")
  printf '  %s  %s:%s  %s\n' "$(basename "$d")" "$VENDOR" "$pid" "$name"
  for i in "$d"/"$(basename "$d")":*; do
    [ -d "$i" ] || continue
    cls=$(cat "$i/bInterfaceClass" 2>/dev/null)
    sub=$(cat "$i/bInterfaceSubClass" 2>/dev/null)
    drv=$(basename "$(readlink "$i/driver" 2>/dev/null)" 2>/dev/null)
    case "$cls" in
      03) label="HID (config channel)" ;;
      01) [ "$sub" = "01" ] && label="Audio Control (mixer only, NO stream)" \
                            || label="Audio Streaming (gives you a PCM)" ;;
      08) label="Mass storage (onboard recordings)" ;;
      ff) label="vendor specific" ;;
      *)  label="class $cls" ;;
    esac
    printf '        if%s  class=%s/%s  %-38s %s\n' \
      "$(cat "$i/bInterfaceNumber" 2>/dev/null)" "$cls" "$sub" "$label" "${drv:-no driver}"
  done
done
[ "$found" -eq 0 ] && bad "no RØDE device found on USB"

# ---------------------------------------------------------------- hidraw nodes
bold "HID nodes"
any_hid=0
for h in /sys/class/hidraw/hidraw*; do
  [ -e "$h" ] || continue
  ids=$(grep -m1 '^HID_ID=' "$h/device/uevent" 2>/dev/null | cut -d= -f2-)
  case "${ids^^}" in *:0000${VENDOR^^}:*) ;; *) continue ;; esac
  any_hid=1
  node="/dev/$(basename "$h")"
  hname=$(grep -m1 '^HID_NAME=' "$h/device/uevent" 2>/dev/null | cut -d= -f2-)
  if [ -r "$node" ] && [ -w "$node" ]; then
    ok "$node  $hname"
  else
    bad "$node  $hname  — not readable/writable by $(id -un)"
  fi
done
[ "$any_hid" -eq 0 ] && warn "no RØDE hidraw node (devices may be absent)"

bold "udev rule"
if [ -e /etc/udev/rules.d/70-rode.rules ]; then
  ok "/etc/udev/rules.d/70-rode.rules present"
elif grep -rlq "$VENDOR" /etc/udev/rules.d/ 2>/dev/null; then
  ok "a rule mentioning $VENDOR exists in /etc/udev/rules.d/"
else
  bad "no udev rule for vendor $VENDOR — see udev/70-rode.rules in this repo"
fi

# ------------------------------------------------------------------ alsa cards
bold "ALSA"
card=""
if [ -r /proc/asound/cards ]; then
  while read -r idx rest; do
    case "$rest" in *RX*|*RODE*|*Wireless*|*NT-USB*|*rode*)
      id=$(cat "/proc/asound/card${idx}/id" 2>/dev/null)
      pcm=$(ls -d /proc/asound/card"${idx}"/pcm* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')
      if [ -z "$pcm" ]; then
        bad "card $idx [$id] exists but has NO PCM device"
        info "AudioControl without AudioStreaming — this is a device mode,"
        info "not a Linux problem. See docs/audio-capture.md."
      else
        ok "card $idx [$id] — pcm: $pcm"
        card="$id"
      fi
      ;;
    esac
  done < <(grep -E '^ *[0-9]+ \[' /proc/asound/cards | sed 's/^ *//;s/\[/ /;s/\]//')
fi
[ -z "$card" ] && warn "no usable RØDE capture card"

# ------------------------------------------------------------------- pipewire
if have wpctl; then
  bold "PipeWire"
  src=$(wpctl status 2>/dev/null | sed -n '/Sources:/,/^ *├\|^ *└/p' | grep -iE 'rode|wireless|RX')
  [ -n "$src" ] && printf '%s\n' "$src" | sed 's/^/  /' || warn "no RØDE source visible to PipeWire"
fi

# ----------------------------------------------------------------- wine bottles
bold "Wine / Bottles"
shopt -s nullglob
bottles=( "$HOME"/.var/app/com.usebottles.bottles/data/bottles/bottles/*/bottle.yml
          "$HOME"/.local/share/bottles/bottles/*/bottle.yml )
if [ ${#bottles[@]} -eq 0 ]; then
  info "no Bottles installation found"
else
  for b in "${bottles[@]}"; do
    bn=$(basename "$(dirname "$b")")
    if grep -A6 'hidraw_devices' "$b" 2>/dev/null | grep -q '0x19F7\|0x19f7'; then
      ok "bottle '$bn' exposes RØDE devices via hidraw_devices"
    else
      warn "bottle '$bn' has no RØDE entry in hidraw_devices"
    fi
  done
fi
pid=$(pgrep -f winedevice.exe 2>/dev/null | tail -1)
if [ -n "$pid" ]; then
  fds=$(readlink /proc/"$pid"/fd/* 2>/dev/null | grep -c hidraw)
  if [ "$fds" -gt 0 ]; then
    ok "winedevice.exe (pid $pid) holds $fds hidraw descriptor(s) open"
  else
    bad "winedevice.exe is running but holds no hidraw descriptor"
  fi
fi

# -------------------------------------------------------------- channel check
if [ "$RECORD" -eq 1 ]; then
  bold "Channel check (${SECONDS_TO_RECORD}s capture)"
  if [ -z "$card" ]; then
    bad "no capture card — nothing to record"
  elif ! have arecord || ! have python3; then
    bad "needs arecord and python3"
  else
    tmp=$(mktemp -t rode-doctor-XXXXXX.wav)
    trap 'rm -f "$tmp"' EXIT
    if arecord -D "hw:$card,0" -f S16_LE -r 48000 -c 2 -d "$SECONDS_TO_RECORD" "$tmp" >/dev/null 2>&1; then
      python3 - "$tmp" <<'PY'
import sys, wave, math, array
w = wave.open(sys.argv[1], 'rb')
n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
a = array.array('h'); a.frombytes(w.readframes(n))
full = 32768.0
for c in range(ch):
    s = a[c::ch]
    peak = max(abs(v) for v in s) if s else 0
    rms = math.sqrt(sum(float(v)*v for v in s)/len(s)) if s else 0
    name = ('left', 'right')[c] if ch == 2 else f'ch{c}'
    if peak == 0:
        print(f"    {name:5}: silent")
        continue
    crest = peak/rms if rms else 0
    near = sum(1 for v in s if abs(v) > 0.8*peak)/len(s)
    kind = "TIMECODE (square wave, not audio)" if crest < 1.5 and peak > 0.5*full else "audio"
    print(f"    {name:5}: peak {20*math.log10(peak/full):6.1f} dBFS  "
          f"crest {crest:5.2f}  near-peak {near*100:5.1f}%  -> {kind}")
PY
    else
      bad "capture failed on hw:$card,0"
    fi
  fi
fi

printf '\n'
