#!/bin/bash
set -e
cd "$(dirname "$0")/server"
if [ ! -d node_modules ]; then npm install --silent; fi
node index.js &
SERVER_PID=$!
echo "Starting server (PID $SERVER_PID)..."
for i in $(seq 1 20); do
  nc -z localhost 2572 2>/dev/null && break
  sleep 0.5
done
echo "Game running at: http://localhost:8086/index.html"
cd ..
npx --yes serve . -p 8086 &
wait
