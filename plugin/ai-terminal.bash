# ai-terminal for bash: SI help inside your normal bash prompt.
#
#   # ...    type a request in plain English after #, press Return: the SI writes the
#            command. Safe ones run straight away; risky ones ask "Run it? y/n" first.
#   why      after a command fails: explains why. Press ↑ for the likely fix.
#            Also takes output from a pipe:  make 2>&1 | why
#   Ctrl+E   (bash 4+) explain the typed command; the first press still jumps to end of line
#   Ctrl+G   (bash 4+) turn the typed line into a command, without running it
#   ait      helper command: ait start|stop|restart|status|key|models|explain <cmd>|why|log
#
# Works with the bash 3.2 that ships with macOS; the key bindings need bash 4+
# (brew install bash). Ghost suggestions need zsh: readline can't draw them.
# Settings are the same as for zsh (see ai-terminal.zsh), set before sourcing.

# Must stay POSIX so other shells can parse it: bail out before any bash-only syntax.
if [ -z "$BASH_VERSION" ]; then
  echo "ai-terminal: ai-terminal.bash is for bash. In zsh, source ai-terminal.zsh instead." >&2
  return 1 2>/dev/null || exit 1
fi
case $- in *i*) ;; *) return 0 ;; esac          # interactive shells only

AIT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
AIT_SOCK=${AIT_SOCK:-$HOME/.ai-terminal.sock}
AIT_PYTHON=${AIT_PYTHON:-$AIT_ROOT/.venv/bin/python}
AIT_EXPLAIN_KEY=${AIT_EXPLAIN_KEY:-'\C-e'}
AIT_TIMEOUT=${AIT_TIMEOUT:-60}
AIT_CONFIRM=${AIT_CONFIRM:-risky}

# _ait_call <type> [start]  — payload on stdin; prints the reply; fails on "ERR:".
_ait_call() {
  [ -x "$AIT_PYTHON" ] || { echo "ERR: run $AIT_ROOT/install.sh first"; return 1; }
  AIT_SOCK=$AIT_SOCK AIT_TIMEOUT=$AIT_TIMEOUT PYTHONPATH="$AIT_ROOT/daemon" \
    "$AIT_PYTHON" -m aitd.client "$1" ${2:+--start}
}

_ait_cols() { echo "${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}"; }

# "1.4s" since $1 (an $EPOCHREALTIME value); empty on bash < 5, which has no clock.
_ait_took() {
  [ -n "$1" ] && [ -n "$EPOCHREALTIME" ] || return 0
  awk -v a="$1" -v b="$EPOCHREALTIME" 'BEGIN { printf "%.1fs", b - a }'
}

_ait_warn() { printf '  \033[33m%s\033[0m\n' "${1#ERR: }"; }
_ait_dim()  { printf '  \033[2m%s\033[0m\n' "$1"; }

# --- after each command: remember it for `why`, run "# requests" ------------------

_ait_hist_last=                    # history number already handled
_ait_last_cmd= _ait_last_status=0

_ait_hist_num() {
  local line num rest
  line=$(HISTTIMEFORMAT= builtin history 1)
  read -r num rest <<<"$line"
  echo "${num%%\*}"                # history marks edited entries with *
}

_ait_prompt_command() {
  local _ait_st=$? _ait_line _ait_num _ait_cmd
  _ait_line=$(HISTTIMEFORMAT= builtin history 1)
  read -r _ait_num _ait_cmd <<<"$_ait_line"
  _ait_num=${_ait_num%%\*}
  if [ -z "$_ait_hist_last" ]; then         # first prompt: only note where history is,
    _ait_hist_last=${_ait_num:-0}           # never act on a line from an old session
    return $_ait_st
  fi
  if [ -n "$_ait_num" ] && [ "$_ait_num" != "$_ait_hist_last" ]; then
    case $_ait_cmd in
      '#'*[![:space:]#]*) _ait_ask "$_ait_cmd" ;;
      why|why\ *|ait|ait\ *|*\|*why*) ;;   # asking about a failure isn't one
      *) _ait_last_cmd=$_ait_cmd _ait_last_status=$_ait_st ;;
    esac
  fi
  if [ -s "$AIT_SOCK.fix" ]; then           # `why` left a fix: ↑ brings it back
    history -s "$(cat "$AIT_SOCK.fix")"
    rm -f "$AIT_SOCK.fix"
  fi
  _ait_hist_last=$(_ait_hist_num)
  return $_ait_st                           # later prompt commands still see the status
}

# Plain English → command. Safe commands run at once; risky ones (and every command
# with AIT_CONFIRM=all) wait for y. Questions back from the SI never run.
_ait_ask() {
  local _ait_out _ait_rc _ait_c _ait_risk= _ait_key _ait_t0=$EPOCHREALTIME
  printf '  \033[2mThinking…\033[0m'
  _ait_out=$(printf '%s' "$1" | _ait_call generate start)
  _ait_rc=$?
  printf '\r\033[K'
  if [ $_ait_rc -ne 0 ] || [ -z "$_ait_out" ]; then
    _ait_warn "${_ait_out:-No answer. Try again.}"
    return
  fi
  _ait_c=${_ait_out%%$'\n'*}
  case $_ait_out in *$'\n'*) _ait_risk=${_ait_out#*$'\n'} ;; esac
  history -s "$_ait_c"                      # ↑ brings it back to edit or rerun
  case $_ait_c in
    '#'*) _ait_dim "Needs more detail. $_ait_c"; return ;;
  esac
  printf '  \033[2mSI · %s\033[0m  %s\n' "$(_ait_took "$_ait_t0")" "$_ait_c"
  if [ -n "$_ait_risk" ] || [ "$AIT_CONFIRM" = all ]; then
    [ -n "$_ait_risk" ] && printf '  \033[33m⚠︎ %s\033[0m ' "$_ait_risk"
    printf 'Run it? y/n '
    read -r -n 1 _ait_key </dev/tty
    echo
    case $_ait_key in
      [yY]) ;;
      *) _ait_dim "Not run. Press ↑ to edit it."; return ;;
    esac
  fi
  eval "$_ait_c"
  _ait_last_cmd=$_ait_c _ait_last_status=$?
}

# --- why: explain the last failed command ----------------------------------------

# The end of this terminal's scrollback in _ait_screen_out; fails where it can't be read.
_ait_screen() {
  [ "${AIT_WHY_SCREEN:-1}" = 1 ] || return 1
  case $TERM_PROGRAM in
    tmux)           _ait_screen_out=$(tmux capture-pane -p -J -S -300 2>/dev/null) ;;
    Apple_Terminal) _ait_screen_out=$(osascript -e 'tell application "Terminal" to get history of selected tab of front window' 2>/dev/null) ;;
    iTerm.app)      _ait_screen_out=$(osascript -e 'tell application "iTerm2" to get contents of current session of current window' 2>/dev/null) ;;
    *)              return 1 ;;
  esac
  [ -n "$_ait_screen_out" ] || return 1
  _ait_screen_out=$(printf '%s' "$_ait_screen_out" | tail -c 20000)
}

_ait_why() {
  local _ait_c= _ait_st _ait_src _ait_out= _ait_reply _ait_rc _ait_fix= _ait_t0=$EPOCHREALTIME
  if [ ! -t 0 ]; then                       # some-command 2>&1 | why
    read -r _ _ait_c <<<"$(HISTTIMEFORMAT= builtin history 1)"
  fi
  case $_ait_c in
    *\|*) _ait_c=${_ait_c%|*} _ait_st='?' _ait_src=pipe
          _ait_out=$(tail -c 20000) ;;
    *)    _ait_c=$_ait_last_cmd _ait_st=$_ait_last_status _ait_src=screen
          _ait_screen && _ait_out=$_ait_screen_out || _ait_src=none ;;
  esac
  _ait_c=${_ait_c%"${_ait_c##*[![:space:]]}"}     # drop trailing spaces
  if [ -z "${_ait_c//[[:space:]]/}" ]; then
    echo "Nothing to explain yet. Run a command first, then: why"
    return 1
  fi
  printf '  \033[2mLooking into it…\033[0m'
  _ait_reply=$(printf '%s\037%s\037%s\037%s' "$_ait_st" "$_ait_c" "$_ait_src" "$_ait_out" \
               | _ait_call "why:$(_ait_cols)" start)
  _ait_rc=$?
  printf '\r\033[K'
  if [ $_ait_rc -ne 0 ]; then
    _ait_warn "$_ait_reply"
    return 1
  fi
  case $_ait_reply in *$'\x1e'*) _ait_fix=${_ait_reply#*$'\x1e'} ;; esac
  printf '\n%s\n\n' "${_ait_reply%%$'\x1e'*}"
  # Runs in a subshell when piped, so hand the fix to the next prompt through a file.
  [ -n "$_ait_fix" ] && printf '%s' "$_ait_fix" >"$AIT_SOCK.fix"
  _ait_dim "${_ait_fix:+Press ↑ for the first fix.  }$(_ait_took "$_ait_t0")"
  echo
}

# --- key bindings (bash 4+: needs READLINE_LINE) ---------------------------------

if [ "${BASH_VERSINFO[0]}" -ge 4 ]; then
  _ait_explain_line() {
    # First press behaves like normal Ctrl+E (end of line); press again at the end to explain.
    if [ -z "${READLINE_LINE//[[:space:]]/}" ] || [ "$READLINE_POINT" -lt "${#READLINE_LINE}" ]; then
      READLINE_POINT=${#READLINE_LINE}
      return
    fi
    local out
    if out=$(printf '%s' "$READLINE_LINE" | _ait_call "explain:$(_ait_cols)" start); then
      printf '\n%s\n\n' "$out"
    else
      _ait_warn "$out"
    fi
  }

  _ait_generate_line() {
    local request=${READLINE_LINE#"${READLINE_LINE%%[![:space:]#]*}"} out risk=
    if [ -z "$request" ]; then
      _ait_dim "Type what you want, then Ctrl+G. For example: make a new folder here"
      return
    fi
    if ! out=$(printf '%s' "$request" | _ait_call generate start); then
      _ait_warn "$out"
      return
    fi
    case $out in *$'\n'*) risk=${out#*$'\n'} ;; esac
    READLINE_LINE=${out%%$'\n'*}
    READLINE_POINT=${#READLINE_LINE}
    [ -n "$risk" ] && _ait_warn "⚠︎ $risk"
  }

  bind -x "\"$AIT_EXPLAIN_KEY\": _ait_explain_line"
  bind -x '"\C-g": _ait_generate_line'
fi

# --- wiring ------------------------------------------------------------------------

case ";$PROMPT_COMMAND;" in
  *_ait_prompt_command*) ;;
  *) PROMPT_COMMAND="_ait_prompt_command${PROMPT_COMMAND:+; $PROMPT_COMMAND}" ;;   # first, to see $?
esac

# `why` unless something else already has the name; `ait why` always works.
if ! alias why >/dev/null 2>&1 && ! type -P why >/dev/null; then
  why() { _ait_why "$@"; }
fi

_ait_daemon_stop() {
  # Stop only the daemon behind this socket, never other ai-terminal daemons.
  local pid=
  [ -r "$AIT_SOCK.pid" ] && pid=$(cat "$AIT_SOCK.pid")
  if [ -n "$pid" ] && ps -o command= -p "$pid" 2>/dev/null | grep -q -- '-m aitd'; then
    kill "$pid" 2>/dev/null
  fi
  rm -f "$AIT_SOCK" "$AIT_SOCK.pid"
}

ait() {
  case $1 in
    start)   _ait_call ping start >/dev/null && echo "started" ;;
    stop)    _ait_daemon_stop; echo "stopped" ;;
    restart) _ait_daemon_stop; _ait_call ping start >/dev/null && echo "restarted" ;;
    status)  _ait_call ping start </dev/null; echo ;;
    explain) shift; printf '%s' "$*" | _ait_call "explain:$(_ait_cols)" start; echo ;;
    why)     _ait_why ;;
    models)  _ait_call models start </dev/null; echo ;;
    suggest) echo "Ghost suggestions need zsh: bash's line editor can't draw them." ;;
    key)
      # The provider is taken from the key itself (nvapi-... is NVIDIA); the
      # optional argument only matters for keys without a known prefix.
      local provider=$2 key service
      read -rs -p "API key (Anthropic sk-ant-... or NVIDIA nvapi-...): " key; echo
      [ -n "$key" ] || { echo "No key entered." >&2; return 1; }
      case $key in
        nvapi-*)  provider=nvidia ;;
        sk-ant-*) provider=anthropic ;;
      esac
      case ${provider:-anthropic} in
        anthropic) provider=anthropic service=ai-terminal ;;
        nvidia)    service=ai-terminal-nvidia ;;
        *)         echo "usage: ait key [anthropic|nvidia]" >&2; return 1 ;;
      esac
      security add-generic-password -U -s "$service" -a "$USER" -w "$key" \
        && echo "Saved $provider key to Keychain (service: $service)." && _ait_daemon_stop ;;
    log)     tail -n 50 ~/Library/Logs/ai-terminal.log ;;
    *)       echo "usage: ait start|stop|restart|status|key|models|explain <command>|why|log" ;;
  esac
}

# Start the daemon in the background so the first request doesn't have to wait.
[ -S "$AIT_SOCK" ] || ( _ait_call ping start </dev/null >/dev/null 2>&1 & )
