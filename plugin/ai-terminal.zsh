# ai-terminal — AI help inside your normal zsh prompt.
#
#   Ctrl+E   on a typed command: explain it (first press still jumps to end of line)
#   ait      helper command: ait start|stop|restart|status|key|explain <cmd>|log
#
# Settings (set before sourcing):
#   AIT_EXPLAIN_KEY   key binding for explain   (default: ^E)
#   AIT_SOCK          daemon socket path        (default: ~/.ai-terminal.sock)
#   AIT_PROVIDER      anthropic | nvidia        (default: whichever has a key, Anthropic first)

# Must stay POSIX so bash can parse it: bail out before any zsh-only syntax.
if [ -z "$ZSH_VERSION" ]; then
  echo "ai-terminal: needs zsh, but this shell isn't zsh. Run 'zsh' first." >&2
  return 1 2>/dev/null || exit 1
fi

0=${(%):-%N}
typeset -g AIT_ROOT=${0:A:h:h}
typeset -g AIT_SOCK=${AIT_SOCK:-$HOME/.ai-terminal.sock}
typeset -g AIT_PYTHON=${AIT_PYTHON:-$AIT_ROOT/.venv/bin/python}
typeset -g AIT_EXPLAIN_KEY=${AIT_EXPLAIN_KEY:-'^E'}
typeset -g AIT_TIMEOUT=${AIT_TIMEOUT:-30}

zmodload zsh/net/socket 2>/dev/null || { print -u2 "ai-terminal: zsh/net/socket unavailable"; return 1 }

# --- daemon management -------------------------------------------------------

_ait_daemon_start() {
  [[ -x $AIT_PYTHON ]] || { print -u2 "ai-terminal: run $AIT_ROOT/install.sh first"; return 1 }
  PYTHONPATH=$AIT_ROOT/daemon nohup $AIT_PYTHON -m aitd >/dev/null 2>&1 &!
  local i
  for i in {1..40}; do
    [[ -S $AIT_SOCK ]] && return 0
    sleep 0.05
  done
  print -u2 "ai-terminal: daemon did not start (see ~/Library/Logs/ai-terminal.log)"
  return 1
}

_ait_daemon_stop() {
  pkill -f "python.* -m aitd" 2>/dev/null
  rm -f $AIT_SOCK
}

# _ait_request <type> <payload>  — prints the daemon's reply, returns 1 on error.
_ait_request() {
  local kind=$1 payload=$2 fd line reply=
  if ! zsocket $AIT_SOCK 2>/dev/null; then
    _ait_daemon_start || return 1
    zsocket $AIT_SOCK 2>/dev/null || { print "ERR: cannot connect to daemon"; return 1 }
  fi
  fd=$REPLY
  print -rn -u $fd -- "$kind"$'\0'"$PWD"$'\0'"$payload"$'\0'
  while IFS= read -r -t $AIT_TIMEOUT -u $fd line || [[ -n $line ]]; do
    reply+=$line$'\n'
    line=
  done
  exec {fd}<&-
  reply=${reply%$'\n'}
  [[ -z $reply ]] && reply="ERR: no reply from daemon (timed out after ${AIT_TIMEOUT}s?)"
  print -r -- "$reply"
  [[ $reply != ERR:* ]]
}

# --- widgets -----------------------------------------------------------------

_ait_print_above() {
  # Print text above the prompt, keeping the line being edited intact.
  zle -I
  print -r -- "$1"
}

_ait_explain_widget() {
  # First press behaves like normal Ctrl+E (end of line); press again at the end to explain.
  if [[ -z ${BUFFER//[[:space:]]/} || $CURSOR -lt ${#BUFFER} ]]; then
    zle end-of-line
    return
  fi
  zle -M "ai-terminal: explaining…"
  zle -R
  local out
  if out=$(_ait_request explain "$BUFFER"); then
    _ait_print_above $'\n'"$out"$'\n'
  else
    _ait_print_above $'\n\e[31m'"${out#ERR: }"$'\e[0m\n'
  fi
  zle -M ""
}

zle -N ait-explain _ait_explain_widget
bindkey "$AIT_EXPLAIN_KEY" ait-explain

# --- ait command -------------------------------------------------------------

ait() {
  case $1 in
    start)   [[ -S $AIT_SOCK ]] && zsocket $AIT_SOCK 2>/dev/null && { exec {REPLY}<&-; print "already running"; return }
             _ait_daemon_start && print "started" ;;
    stop)    _ait_daemon_stop; print "stopped" ;;
    restart) _ait_daemon_stop; _ait_daemon_start && print "restarted" ;;
    status)  _ait_request ping "" ;;
    explain) shift; _ait_request explain "$*" ;;
    key)
      # The provider is taken from the key itself (nvapi-... is NVIDIA); the
      # optional argument only matters for keys without a known prefix.
      local provider=$2 key
      read -rs "key?API key (Anthropic sk-ant-... or NVIDIA nvapi-...): "; print
      [[ -n $key ]] || { print -u2 "No key entered."; return 1 }
      case $key in
        nvapi-*)  provider=nvidia ;;
        sk-ant-*) provider=anthropic ;;
      esac
      case ${provider:-anthropic} in
        anthropic) provider=anthropic; local service=ai-terminal ;;
        nvidia)    local service=ai-terminal-nvidia ;;
        *)         print -u2 "usage: ait key [anthropic|nvidia]"; return 1 ;;
      esac
      security add-generic-password -U -s $service -a "$USER" -w "$key" \
        && print "Saved $provider key to Keychain (service: $service)." && _ait_daemon_stop ;;
    log)     tail -n 50 ~/Library/Logs/ai-terminal.log ;;
    *)       print "usage: ait start|stop|restart|status|key|explain <command>|log" ;;
  esac
}
