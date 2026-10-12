#!/bin/bash
# 2-worker bulk runner
# Usage: EMAIL='you@gmail.com' PASS='StrongPass!1' ./bulk.sh <start> <count>
# Bulk runner 2-worker — W1: 16-20, W2: 21-25 (offset biar gak tabrakan)
# Usage: ./bulk.sh <start> <count>
START=${1:-26}; COUNT=${2:-5}
MID=$((START + COUNT))
(
  for n in $(seq $START $((MID-1))); do
    timeout 780 python3 -u kgriver.py "$EMAIL+alias$n@gmail.com" '$PASS' 2>&1 | grep -E "BALANCE|RESULT|no token|OTP timeout"
    sleep 45
  done
) > w1.log 2>&1 &
W1=$!
(
  sleep 90
  for n in $(seq $MID $((START+2*COUNT-1))); do
    timeout 780 python3 -u kgriver.py "$EMAIL+alias$n@gmail.com" '$PASS' 2>&1 | grep -E "BALANCE|RESULT|no token|OTP timeout"
    sleep 45
  done
) > w2.log 2>&1 &
W2=$!
wait $W1 $W2
echo "BULK DONE"
python3 -c "
import json
v=[json.loads(l) for l in open('river_accounts.jsonl') if json.loads(l).get('api_key')]
print(f'verified: {len(v)} | \$={len(v)*25}')"
