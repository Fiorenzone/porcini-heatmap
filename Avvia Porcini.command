#!/bin/bash
cd "$(dirname "$0")"

# Kill vecchio server su 8765 se ancora vivo
old=$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null)
if [ -n "$old" ]; then
  kill $old 2>/dev/null
  sleep 0.5
fi

python3 server.py &
pid=$!

# Forest load ~45s a freddo: aspetta fino a 90s
ready=0
for i in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:8765/api/status" >/dev/null; then
    ready=1
    break
  fi
  # Se python morto → stop
  if ! kill -0 $pid 2>/dev/null; then
    echo "Server crash. Guarda Terminal."
    wait $pid
    exit 1
  fi
  sleep 0.5
done

if [ "$ready" -ne 1 ]; then
  echo "Server non pronto dopo 90s. Connessione rifiutata evitata."
  kill $pid 2>/dev/null
  exit 1
fi

if open -Ra "Google Chrome"; then
  open -a "Google Chrome" "http://127.0.0.1:8765"
else
  open "http://127.0.0.1:8765"
fi
wait $pid
