#!/bin/bash

echo "Starting Warehouse Swarm Intelligence System"
echo "=========================================="
echo ""

echo "Starting Backend Server..."
cd "$(dirname "$0")"
uv run python -m backend.api.main &
BACKEND_PID=$!

echo "Waiting for backend to initialize..."
sleep 5

echo ""
echo "Starting Frontend Development Server..."
cd frontend
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
