# Shelldon (Intelligence in Terminal): SI help inside your normal zsh prompt.
#
#   typing   grey "ghost" suggestion after the cursor: from your history instantly,
#            from the SI after a short pause.  → or End accepts it.
#   Ctrl+E   on a typed command: explain it (first press still jumps to end of line).
#            You can keep typing while it works; the answer prints above the prompt.
#   # ...    type a request in plain English after #, press Return: the SI writes the
#            command. Safe ones run straight away; risky ones ask "Run it? y/n" first.
#            Ctrl+G does the same for any line without the #.
#   why      after a command fails: explains why, and puts the likely fix on the prompt.
#            Also takes output from a pipe:  make 2>&1 | why
#   ait      helper command: ait start|stop|restart|status|key|models|suggest on|off|explain <cmd>|why|log
#
# Settings (set before sourcing):
#   AIT_EXPLAIN_KEY     key binding for explain              (default: ^E)
#   AIT_SUGGEST         1 = show ghost suggestions           (default: 1)
#   AIT_AI_SUGGEST      1 = ask the SI when history has none (default: 1)
#   AIT_SUGGEST_DELAY   pause in seconds before asking the SI (default: 0.8)
#   AIT_SUGGEST_STYLE   highlight for the ghost text          (default: fg=8)
#   AIT_WHY_SCREEN      1 = `why` reads the end of the terminal screen  (default: 1)
#   AIT_CONFIRM         risky = run safe SI commands at once, ask y/n for risky ones;
#                       all = always ask                      (default: risky)
#   AIT_SOCK            daemon socket path                   (default: ~/.ai-terminal.sock)
#   AIT_PROVIDER        anthropic | nvidia                   (default: whichever has a key)

# Must stay POSIX so bash can parse it: bail out before any zsh-only syntax.
if [ -z "$ZSH_VERSION" ]; then
  echo "shelldon: needs zsh, but this shell isn't zsh. Run 'zsh' first." >&2
  return 1 2>/dev/null || exit 1
fi

0=${(%):-%N}
typeset -g AIT_ROOT=${0:A:h:h}
typeset -g AIT_SOCK=${AIT_SOCK:-$HOME/.ai-terminal.sock}
typeset -g AIT_PYTHON=${AIT_PYTHON:-$AIT_ROOT/.venv/bin/python}
typeset -g AIT_EXPLAIN_KEY=${AIT_EXPLAIN_KEY:-'^E'}
typeset -g AIT_TIMEOUT=${AIT_TIMEOUT:-60}
typeset -g AIT_SUGGEST=${AIT_SUGGEST:-1}
typeset -g AIT_AI_SUGGEST=${AIT_AI_SUGGEST:-1}
typeset -g AIT_SUGGEST_DELAY=${AIT_SUGGEST_DELAY:-0.8}
typeset -g AIT_SUGGEST_STYLE=${AIT_SUGGEST_STYLE:-fg=8}
typeset -g AIT_CONFIRM=${AIT_CONFIRM:-risky}

zmodload zsh/net/socket 2>/dev/null || { print -u2 "shelldon: zsh/net/socket unavailable"; return 1 }
zmodload zsh/parameter 2>/dev/null
zmodload zsh/datetime 2>/dev/null

# Seconds since $1 (an $EPOCHREALTIME value), formatted like "1.8s", in REPLY.
_ait_elapsed() {
  local -F secs=$(( EPOCHREALTIME - $1 ))
  REPLY=$(printf '%.1fs' $secs)
}

# --- daemon management -------------------------------------------------------

_ait_daemon_spawn() {
  [[ -x $AIT_PYTHON ]] || return 1
  PYTHONPATH=$AIT_ROOT/daemon nohup $AIT_PYTHON -m aitd >/dev/null 2>&1 &!
}

_ait_daemon_start() {
  [[ -x $AIT_PYTHON ]] || { print -u2 "shelldon: run $AIT_ROOT/install.sh first"; return 1 }
  _ait_daemon_spawn
  local i
  for i in {1..40}; do
    [[ -S $AIT_SOCK ]] && return 0
    sleep 0.05
  done
  print -u2 "shelldon: daemon did not start (see ~/Library/Logs/ai-terminal.log)"
  return 1
}

_ait_daemon_stop() {
  # Stop only the daemon behind this socket, never other Shelldon daemons.
  local pid pids
  [[ -r $AIT_SOCK.pid ]] && pid=$(<$AIT_SOCK.pid)
  if [[ $pid == <-> ]] && ps -o command= -p $pid 2>/dev/null | grep -q -- '-m aitd'; then
    kill $pid 2>/dev/null
  else                                   # daemon from before pid files existed
    pids=(${(f)"$(lsof -t $AIT_SOCK 2>/dev/null)"})
    for pid in $pids; do
      ps -o command= -p $pid 2>/dev/null | grep -q -- '-m aitd' && kill $pid 2>/dev/null
    done
  fi
  rm -f $AIT_SOCK $AIT_SOCK.pid
}

# _ait_send <type> <payload> [start]  — sends a request; the socket fd is left in REPLY.
# With "start", launches the daemon if it isn't running (blocks briefly).
_ait_send() {
  if ! zsocket $AIT_SOCK 2>/dev/null; then
    [[ $3 == start ]] || return 1
    _ait_daemon_start || return 1
    zsocket $AIT_SOCK 2>/dev/null || return 1
  fi
  print -rn -u $REPLY -- "$1"$'\0'"$PWD"$'\0'"$2"$'\0'
}

# _ait_request <type> <payload>  — synchronous: prints the reply, returns 1 on error.
_ait_request() {
  local fd reply=
  _ait_send "$1" "$2" start || { print "ERR: cannot reach the Shelldon daemon"; return 1 }
  fd=$REPLY
  IFS= read -r -d '' -t $AIT_TIMEOUT -u $fd reply
  exec {fd}<&-
  [[ -z $reply ]] && reply="ERR: no reply from daemon (timed out after ${AIT_TIMEOUT}s?)"
  print -r -- "$reply"
  [[ $reply != ERR:* ]]
}

# --- explain (Ctrl+E), asynchronous -------------------------------------------

typeset -g _ait_explain_fd=0 _ait_explain_t0=0

_ait_print_above() {
  # Print text above the prompt, keeping the line being edited intact. Called from
  # a zle -F handler, so the prompt must be redrawn explicitly afterwards.
  zle -I
  print -r -- "$1"
  zle reset-prompt
  zle -R
}

_ait_explain_widget() {
  # First press behaves like normal Ctrl+E (end of line); press again at the end to explain.
  if [[ -z ${BUFFER//[[:space:]]/} || $CURSOR -lt ${#BUFFER} ]]; then
    zle end-of-line
    return
  fi
  if (( _ait_explain_fd )); then              # a newer request replaces the pending one
    zle -F $_ait_explain_fd 2>/dev/null; exec {_ait_explain_fd}<&-
  fi
  if ! _ait_send "explain:${COLUMNS:-80}" "$BUFFER" start; then
    zle -M "Can't reach Shelldon. Try: ait restart"
    return
  fi
  _ait_explain_fd=$REPLY
  _ait_explain_t0=$EPOCHREALTIME
  zle -F -w $_ait_explain_fd _ait_explain_ready
  zle -M "Explaining…"
}

_ait_explain_ready() {
  local fd=$1 out=
  zle -F $fd
  IFS= read -r -d '' -u $fd out
  exec {fd}<&-
  _ait_explain_fd=0
  zle -M ""
  if [[ -z $out ]]; then
    _ait_print_above $'\n  \e[33m'"No answer. The daemon may have restarted."$'\e[0m\n'
  elif [[ $out == ERR:* ]]; then
    _ait_print_above $'\n  \e[33m'"${out#ERR: }"$'\e[0m\n'
  else
    _ait_elapsed $_ait_explain_t0
    _ait_print_above $'\n'"$out"$'\n\n  \e[2m'"$REPLY"$'\e[0m\n'
  fi
}

zle -N ait-explain _ait_explain_widget
zle -N _ait_explain_ready
bindkey "$AIT_EXPLAIN_KEY" ait-explain

# --- ask in plain English: "# request" + Return, or Ctrl+G --------------------
# The SI's command replaces the line. Safe commands run at once; risky ones (and
# every command with AIT_CONFIRM=all) wait for y. Questions back from the SI never run.
# This waits in place (you just pressed Return and want the result); Ctrl+C or Esc cancels.

_ait_run_line() {
  _ait_cancel_suggest
  _ait_hide_ghost
  zle .accept-line
}

_ait_generate_start() {
  local request=$BUFFER fd key out= cmd risk= t0=$EPOCHREALTIME
  _ait_cancel_suggest
  _ait_hide_ghost
  if ! _ait_send generate "$request" start; then
    zle -M "Can't reach Shelldon. Try: ait restart"
    return
  fi
  fd=$REPLY
  zle -M "Thinking…"
  zle -R
  local first= cancelled=0
  setopt localoptions localtraps
  trap 'cancelled=1' INT                           # Ctrl+C arrives as a signal, not a key
  while true; do                                   # wait for the answer
    IFS= read -r -k 1 -t 0.1 -u $fd first && break
    if (( cancelled )) || { read -k 1 -t 0 key && [[ $key == $'\e' ]] }; then
      exec {fd}<&-
      zle -M "Cancelled."
      return
    fi
    if (( EPOCHREALTIME - t0 > AIT_TIMEOUT )); then
      exec {fd}<&-
      zle -M "No answer after ${AIT_TIMEOUT}s. Try again."
      return
    fi
  done
  IFS= read -r -d '' -u $fd out
  out=$first$out
  exec {fd}<&-
  if [[ -z $out || $out == ERR:* ]]; then
    zle -M "${${out#ERR: }:-No answer. Try again.}"
    return
  fi
  cmd=${out%%$'\n'*}
  [[ $out == *$'\n'* ]] && risk=${out#*$'\n'}
  _ait_elapsed $t0
  local took=$REPLY
  BUFFER=$cmd
  CURSOR=${#BUFFER}
  _ait_seen=$BUFFER                                # no ghost over the SI's command

  if [[ $cmd == \#* ]]; then                       # the SI is asking for more detail
    zle -M "Needs more detail. Edit the line and press Return.  $took"
    return
  fi
  if [[ -z $risk && $AIT_CONFIRM != all ]]; then
    zle -M ""
    _ait_print_above $'  \e[2m'"SI · $took"$'\e[0m'
    _ait_run_line
    return
  fi
  zle -M "${risk:+⚠︎ $risk }Run it? y/n  $took"
  zle -R
  read -k 1 key
  if [[ $key == [yY] ]]; then
    zle -M ""
    _ait_run_line
  else
    zle -M "Not run. Edit it, or press Return to run it."
  fi
}

_ait_generate_widget() {
  if [[ -z ${BUFFER//[[:space:]]/} ]]; then
    zle -M "Type what you want, then Ctrl+G. For example: make a new folder here"
    return
  fi
  _ait_generate_start
}

zle -N ait-generate _ait_generate_widget
bindkey '^G' ait-generate

# --- ghost suggestions -------------------------------------------------------

typeset -g _ait_ghost= _ait_seen= _ait_suggest_for=
typeset -g _ait_timer_fd=0 _ait_suggest_fd=0

_ait_show_ghost() {
  _ait_ghost=$1
  POSTDISPLAY=$1
  region_highlight=(${region_highlight:#*memo=ait-ghost})
  region_highlight+=("${#BUFFER} $(( ${#BUFFER} + ${#POSTDISPLAY} )) $AIT_SUGGEST_STYLE memo=ait-ghost")
}

_ait_hide_ghost() {
  _ait_ghost=
  POSTDISPLAY=
  region_highlight=(${region_highlight:#*memo=ait-ghost})
}

_ait_cancel_suggest() {
  local fd
  for fd in $_ait_timer_fd $_ait_suggest_fd; do
    (( fd )) || continue
    zle -F $fd 2>/dev/null
    exec {fd}<&-
  done
  _ait_timer_fd=0 _ait_suggest_fd=0
}

# Newest history entry that starts with $1 (and is longer); the remainder goes in REPLY.
_ait_history_suffix() {
  local prefix=$1 k
  local -a keys
  keys=(${(k)history[(R)${(b)prefix}?*]})
  for k in ${(On)keys}; do
    [[ $history[$k] == *$'\n'* ]] && continue
    REPLY=${history[$k]#"$prefix"}
    return 0
  done
  return 1
}

_ait_on_redraw() {
  (( AIT_SUGGEST )) || return
  [[ $BUFFER == "$_ait_seen" ]] && return
  local prev=$_ait_seen full
  _ait_seen=$BUFFER
  _ait_cancel_suggest

  # Typed the next characters of the ghost: keep showing the rest of it.
  full=$prev$_ait_ghost
  if [[ -n $_ait_ghost && $BUFFER == "$prev"* && $full == "$BUFFER"?* ]]; then
    _ait_show_ghost "${full#"$BUFFER"}"
    return
  fi
  _ait_hide_ghost
  (( CURSOR == ${#BUFFER} )) || return
  [[ -n ${BUFFER//[[:space:]]/} ]] || return

  if _ait_history_suffix "$BUFFER"; then
    _ait_show_ghost "$REPLY"
  elif (( AIT_AI_SUGGEST && ${#BUFFER} >= 3 )) && [[ $BUFFER != \#* ]]; then
    _ait_suggest_for=$BUFFER                  # wait for a pause in typing, then ask
    exec {_ait_timer_fd}< <(sleep $AIT_SUGGEST_DELAY; print .)
    zle -F -w $_ait_timer_fd _ait_timer_fired
  fi
}

_ait_still_wanted() {
  [[ $BUFFER == "$_ait_suggest_for" && $CURSOR -eq ${#BUFFER} && -z $_ait_ghost ]]
}

_ait_timer_fired() {
  local fd=$1
  zle -F $fd
  exec {fd}<&-
  _ait_timer_fd=0
  _ait_still_wanted || return
  _ait_send suggest "$BUFFER" || return       # never block typing to start the daemon
  _ait_suggest_fd=$REPLY
  zle -F -w $_ait_suggest_fd _ait_suggest_ready
}

_ait_suggest_ready() {
  local fd=$1 out=
  zle -F $fd
  IFS= read -r -d '' -u $fd out
  exec {fd}<&-
  _ait_suggest_fd=0
  [[ -n $out && $out != ERR:* ]] || return
  _ait_still_wanted || return
  _ait_show_ghost "${out%%$'\n'*}"
  zle -R
}

# → / End / Ctrl+F accept the ghost when the cursor is at the end; otherwise act normally.
_ait_accept_or() {
  if [[ -n $_ait_ghost && $CURSOR -eq ${#BUFFER} ]]; then
    BUFFER+=$_ait_ghost
    CURSOR=${#BUFFER}
    _ait_hide_ghost
    _ait_seen=$BUFFER
  else
    zle $1
  fi
}
_ait_accept_forward_char() { _ait_accept_or .forward-char }
_ait_accept_end_of_line()  { _ait_accept_or .end-of-line }

_ait_accept_line() {
  if [[ $BUFFER =~ '^[[:space:]]*#[[:space:]]*[^[:space:]]' ]]; then
    _ait_generate_start                        # "# make a new folder here" → a command
    return
  fi
  _ait_cancel_suggest
  _ait_hide_ghost                              # don't leave grey text in the scrollback
  zle .accept-line
}

_ait_line_init() {
  _ait_seen=
  _ait_cancel_suggest
  _ait_hide_ghost
}

zle -N _ait_timer_fired
zle -N _ait_suggest_ready
zle -N _ait_accept_forward_char
zle -N _ait_accept_end_of_line
zle -N accept-line _ait_accept_line
zle -N _ait_on_redraw
zle -N _ait_line_init

autoload -Uz add-zle-hook-widget
add-zle-hook-widget line-pre-redraw _ait_on_redraw
add-zle-hook-widget line-init _ait_line_init

for _ait_km in emacs viins; do
  bindkey -M $_ait_km '^[[C' _ait_accept_forward_char   # →
  bindkey -M $_ait_km '^[OC' _ait_accept_forward_char   # → (application mode)
  bindkey -M $_ait_km '^F'   _ait_accept_forward_char
  bindkey -M $_ait_km '^[[F' _ait_accept_end_of_line    # End
  bindkey -M $_ait_km '^[OF' _ait_accept_end_of_line
done
unset _ait_km

# --- why: explain the last failed command ------------------------------------
# Hooks remember the last command and its exit status. The output isn't kept by the
# shell, so `why` reads the end of the terminal screen (Terminal.app, iTerm2, tmux),
# or takes it from a pipe:  make 2>&1 | why

typeset -g _ait_cmd= _ait_last_cmd= _ait_last_status=0

_ait_preexec() { _ait_cmd=$1 }

_ait_precmd() {
  local st=$?                                  # must stay first
  [[ -n $_ait_cmd ]] || return 0
  if [[ ${${(z)_ait_cmd}[1]} != (why|ait) ]]; then
    _ait_last_cmd=$_ait_cmd _ait_last_status=$st
  fi
  _ait_cmd=
}

# The end of this terminal's scrollback in REPLY; fails where it can't be read.
_ait_screen() {
  (( ${AIT_WHY_SCREEN:-1} )) || return 1
  case $TERM_PROGRAM in
    tmux)           REPLY=$(tmux capture-pane -p -J -S -300 2>/dev/null) ;;
    Apple_Terminal) REPLY=$(osascript -e 'tell application "Terminal" to get history of selected tab of front window' 2>/dev/null) ;;
    iTerm.app)      REPLY=$(osascript -e 'tell application "iTerm2" to get contents of current session of current window' 2>/dev/null) ;;
    *)              return 1 ;;
  esac
  [[ -n $REPLY ]] || return 1
  (( ${#REPLY} > 20000 )) && REPLY=${REPLY[-20000,-1]}
  return 0
}

_ait_why() {
  setopt localoptions extendedglob
  local cmd st src out= reply rc t0=$EPOCHREALTIME
  if [[ ! -t 0 && $_ait_cmd == *\|* ]]; then   # some-command 2>&1 | why
    cmd=${_ait_cmd%\|*} st='?' src=pipe
    out=$(tail -c 20000)
  else
    cmd=$_ait_last_cmd st=$_ait_last_status src=screen
    _ait_screen && out=$REPLY || src=none
  fi
  cmd=${${cmd##[[:space:]]#}%%[[:space:]]#}
  if [[ -z $cmd ]]; then
    print "Nothing to explain yet. Run a command first, then: why"
    return 1
  fi
  print -n $'  \e[2mLooking into it…\e[0m'
  reply=$(_ait_request "why:${COLUMNS:-80}" "$st"$'\x1f'"$cmd"$'\x1f'"$src"$'\x1f'"$out")
  rc=$?
  print -n $'\r\e[K'
  if (( rc )); then
    print -r -- $'  \e[33m'"${reply#ERR: }"$'\e[0m'
    return 1
  fi
  local text=${reply%%$'\x1e'*} fix=
  [[ $reply == *$'\x1e'* ]] && fix=${reply#*$'\x1e'}
  _ait_elapsed $t0
  print -r -- $'\n'"$text"$'\n\n  \e[2m'"${fix:+First fix is on your prompt. Return runs it.  }$REPLY"$'\e[0m\n'
  [[ -n $fix ]] && print -z -- "$fix"
  return 0
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec _ait_preexec
precmd_functions=(_ait_precmd ${precmd_functions:#_ait_precmd})   # first, to see $?

# `why` unless something else already has the name; `ait why` always works.
(( $+commands[why] || $+aliases[why] )) || why() { _ait_why "$@" }

# Start the daemon in the background so the first suggestion doesn't have to wait.
[[ -S $AIT_SOCK ]] || _ait_daemon_spawn

# --- ait command -------------------------------------------------------------

ait() {
  case $1 in
    start)   [[ -S $AIT_SOCK ]] && zsocket $AIT_SOCK 2>/dev/null && { exec {REPLY}<&-; print "already running"; return }
             _ait_daemon_start && print "started" ;;
    stop)    _ait_daemon_stop; print "stopped" ;;
    restart) _ait_daemon_stop; _ait_daemon_start && print "restarted" ;;
    status)  _ait_request ping "" ;;
    explain) shift; _ait_request "explain:${COLUMNS:-80}" "$*" ;;
    why)     _ait_why ;;
    suggest)
      case $2 in
        on)  AIT_SUGGEST=1 AIT_AI_SUGGEST=1; print "suggestions on (history + AI)" ;;
        history) AIT_SUGGEST=1 AIT_AI_SUGGEST=0; print "suggestions from history only" ;;
        off) AIT_SUGGEST=0; print "suggestions off" ;;
        *)   print "usage: ait suggest on|history|off" ;;
      esac ;;
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
    models)  _ait_request models "" ;;
    log)     tail -n 50 ~/Library/Logs/ai-terminal.log ;;
    *)       print "usage: ait start|stop|restart|status|key|models|suggest on|history|off|explain <command>|why|log" ;;
  esac
}
