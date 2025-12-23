#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
type_into_app.sh

Usage:
  type_into_app.sh --app "App Name" --text "hello" [options]
  type_into_app.sh --bundle-id com.example.app --text -

Options:
  --app <name>         App name to activate (e.g. "Promptlight")
  --bundle-id <id>     Bundle id to activate (e.g. com.example.app)
  --text <text|- >     Text to type, "-" reads stdin
  --enter              Press Enter after typing
  --delay-ms <ms>      Delay after activate (default: 200)
  --timeout-sec <sec>  AppleScript timeout (default: 30)
  --click <x,y>        Click screen coords before typing (e.g. 520,420)
  --click-rel <x,y>    Click coords relative to window top-left
  --tabs <n>           Press Tab n times before typing
  --no-activate        Skip app activate; type into frontmost app
  -h, --help           Show help

Notes:
  - Requires macOS Accessibility + Automation permission for the terminal.
  - Use --text - for multiline input (pipe stdin).
EOF
}

app=""
bundle_id=""
text=""
enter=0
delay_ms=200
timeout_sec=30
click=""
click_rel=""
tabs=0
no_activate=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      app="${2:-}"
      shift 2
      ;;
    --bundle-id)
      bundle_id="${2:-}"
      shift 2
      ;;
    --text)
      text="${2:-}"
      shift 2
      ;;
    --enter)
      enter=1
      shift
      ;;
    --delay-ms)
      delay_ms="${2:-}"
      shift 2
      ;;
    --timeout-sec)
      timeout_sec="${2:-}"
      shift 2
      ;;
    --no-activate)
      no_activate=1
      shift
      ;;
    --click)
      click="${2:-}"
      shift 2
      ;;
    --click-rel)
      click_rel="${2:-}"
      shift 2
      ;;
    --tabs)
      tabs="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

app_ref="${bundle_id:-$app}"
if [[ -z "${app_ref}" && "${no_activate}" -ne 1 ]]; then
  usage >&2
  exit 1
fi

if [[ -n "${click_rel}" && -z "${app_ref}" ]]; then
  echo "error: --click-rel requires --app or --bundle-id" >&2
  exit 1
fi

if [[ "${text}" == "-" ]]; then
  text="$(cat)"
elif [[ -z "${text}" ]]; then
  if [[ ! -t 0 ]]; then
    text="$(cat)"
  fi
fi

delay_sec=$(awk -v ms="${delay_ms}" 'BEGIN { printf "%.3f", ms/1000 }')
click_x=""
click_y=""
if [[ -n "${click}" ]]; then
  IFS=',' read -r click_x click_y <<<"${click}"
fi

click_rel_x=""
click_rel_y=""
if [[ -n "${click_rel}" ]]; then
  IFS=',' read -r click_rel_x click_rel_y <<<"${click_rel}"
fi

app_mode="name"
if [[ -n "${bundle_id}" ]]; then
  app_mode="id"
fi

if ! err=$(osascript -e '
on run argv
  set appRef to item 1 of argv
  set appMode to item 2 of argv
  set textToType to item 3 of argv
  set shouldEnter to item 4 of argv
  set delaySeconds to item 5 of argv as number
  set clickX to item 6 of argv
  set clickY to item 7 of argv
  set tabCount to item 8 of argv as integer
  set timeoutSeconds to item 9 of argv as number
  set noActivate to item 10 of argv as integer
  set clickRelX to item 11 of argv
  set clickRelY to item 12 of argv

  with timeout of timeoutSeconds seconds
  if noActivate is 0 then
    if appMode is "id" then
      tell application id appRef to activate
    else
      tell application appRef to activate
    end if
  end if

  if delaySeconds > 0 then delay delaySeconds

  tell application "System Events"
    if clickRelX is not "" and clickRelY is not "" then
      set relX to clickRelX as integer
      set relY to clickRelY as integer
      if appMode is "id" then
        tell application id appRef to set procName to name
      else
        set procName to appRef
      end if
      tell application "System Events" to set winPos to position of window 1 of process procName
      set absX to (item 1 of winPos) + relX
      set absY to (item 2 of winPos) + relY
      click at {absX, absY}
      delay 0.1
    else if clickX is not "" and clickY is not "" then
      click at {clickX as integer, clickY as integer}
      delay 0.1
    end if

    repeat tabCount times
      key code 48
      delay 0.05
    end repeat

    repeat with i from 1 to length of textToType
      set ch to character i of textToType
      if ch is return or ch is linefeed then
        key code 36
      else
        keystroke ch
      end if
    end repeat

    if shouldEnter is "1" then
      key code 36
    end if
  end tell
  end timeout
end run
' -- "${app_ref}" "${app_mode}" "${text}" "${enter}" "${delay_sec}" "${click_x}" "${click_y}" "${tabs}" "${timeout_sec}" "${no_activate}" "${click_rel_x}" "${click_rel_y}" 2>&1); then
  if echo "${err}" | grep -Eqi "not authorized|not allowed|accessibility|automation|timed out"; then
    echo "error: Accessibility/Automation permission missing for terminal/osascript" >&2
    echo "fix: System Settings -> Privacy & Security -> Accessibility -> enable terminal app" >&2
    echo "fix: System Settings -> Privacy & Security -> Automation -> enable terminal app -> System Events" >&2
    echo "fix: System Settings -> Privacy & Security -> Automation -> enable terminal app -> target app" >&2
    echo "tip: open \"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility\"" >&2
    exit 1
  fi
  echo "error: osascript failed: ${err}" >&2
  exit 1
fi
