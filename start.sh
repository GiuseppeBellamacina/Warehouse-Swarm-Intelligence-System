#!/bin/bash

echo "Starting Warehouse Swarm Intelligence System"
echo "=========================================="
echo ""

# Save the root directory path
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting Backend Server..."
cd "$ROOT_DIR"
uv run python -m backend.api.main &
BACKEND_PID=$!

echo "Waiting for backend to initialize..."
sleep 5

echo ""
echo "Starting Frontend Development Server..."
cd "$ROOT_DIR/frontend"
bun run dev &
FRONTEND_PID=$!

echo ""
echo "=========================================="
echo "System is running!"
echo ""
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo "API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for interrupt
trap "kill $BACKEND_PID $FRONTEND_PID; exit" INT
wait
