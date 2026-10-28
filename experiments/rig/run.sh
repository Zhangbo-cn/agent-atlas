#!/usr/bin/env bash
set -uo pipefail
# usage: run.sh <scenario> <keepRecentTokens> <label>
scenario="$1"; keep="$2"; label="$3"
root=/tmp/pi-lab
out="$root/runs/$label"
rm -rf "$out"; mkdir -p "$out" "$root/log"
rm -f "$root/log/"*.json

cat > "$root/agent/settings.json" <<EOF
{
  "defaultProvider": "lab",
  "defaultModel": "lab-tiny",
  "compaction": { "enabled": true, "reserveTokens": 2500, "keepRecentTokens": $keep }
}
EOF

curl -s "http://127.0.0.1:4873/reset?scenario=$scenario" > "$out/reset.json"

cd "$root/work"
PI_CODING_AGENT_DIR="$root/agent" timeout 300 node \
  /home/zhang-pc/pi-src/packages/coding-agent/dist/cli.js \
  --mode json --print --model lab/lab-tiny \
  --no-extensions --no-skills --no-prompt-templates \
  --session-dir "$out/sessions" \
  "Run the diagnostic steps one by one with the bash tool, then report." \
  > "$out/events.jsonl" 2> "$out/stderr.txt"
echo "exit=$?" > "$out/status.txt"

cp "$root/log/"*.json "$out/" 2>/dev/null
cp "$root/requests.log" "$out/requests.log" 2>/dev/null
echo "done $label"
