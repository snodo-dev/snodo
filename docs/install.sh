#!/bin/sh
set -eu

has_snodo=false
old_version=none
if command -v snodo >/dev/null 2>&1; then
    has_snodo=true
    old_version=$(snodo --version 2>/dev/null || printf 'unknown')
fi

if command -v uv >/dev/null 2>&1; then
    uv_path=$(command -v uv)
else
    printf '%s\n' 'uv: not found; installing uv with Astral’s installer.'
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH
    if ! command -v uv >/dev/null 2>&1; then
        printf '%s\n' 'uv installation completed, but uv was not found on PATH.' >&2
        exit 1
    fi
    uv_path=$(command -v uv)
fi

uv_version=$(uv --version)
printf 'Snodo: %s\n' "$old_version"
printf 'uv: %s (%s)\n' "$uv_version" "$uv_path"

if [ "$has_snodo" = true ]; then
    uv tool upgrade snodo
else
    uv tool install snodo
fi

new_version=$(snodo --version)

if [ -t 1 ] && [ "${NO_COLOR+x}" != x ]; then
    grey=$(printf '\033[38;2;220;227;230m')
    teal=$(printf '\033[38;2;44;207;188m')
    reset=$(printf '\033[0m')
    printf '%s\n' "${grey}╭──  ┌─╮  ╭─╮  ╭─┤  ╭─╮   ╭─┤  ╭─╮  ╷   ╷${reset}"
    printf '%s\n' "${grey}╰─╮  │ │  │ │  │ │  │ │   │ │  ├─╯  ╰╮ ╭╯${reset}"
    printf '%s' "$grey"
    printf '──╯  ╵ ╵  ╰─╯  ╰─╯  ╰─╯ '
    printf '%s▪%s' "$teal" "$grey"
    printf ' ╰─╯  ╰──   ╰─╯%s\n' "$reset"
    printf '%sdev%s\n' "$teal" "$reset"
else
    cat <<'WORDMARK'
╭──  ┌─╮  ╭─╮  ╭─┤  ╭─╮   ╭─┤  ╭─╮  ╷   ╷
╰─╮  │ │  │ │  │ │  │ │   │ │  ├─╯  ╰╮ ╭╯
──╯  ╵ ╵  ╰─╯  ╰─╯  ╰─╯ ▪ ╰─╯  ╰──   ╰─╯
WORDMARK
    printf '%s\n' 'dev'
fi

if [ "$has_snodo" = true ]; then
    printf 'Updated %s → %s\n' "$old_version" "$new_version"
    printf '%s\n' 'A running snodo serve picks up this version the next time your agent reconnects.'
else
    printf 'Installed %s\n' "$new_version"
fi

printf '%s\n' 'Next steps:' '  cd into a git repository' '  snodo init' '  snodo'
printf '%s\n' 'Docs: https://docs.snodo.dev'
