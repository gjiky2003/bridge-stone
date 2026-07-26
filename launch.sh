#!/bin/bash
# BridgeStone Capital — Launch Script
# Usage: ./launch.sh

echo "======================================="
echo "  BridgeStone Capital — Launching..."
echo "======================================="

cd "$(dirname "$0")"

# Install deps
echo "[1/4] Installing dependencies..."
pip install -q -r requirements.txt 2>/dev/null

# Seed data
echo "[2/4] Seeding database..."
python3 scripts/seed_data.py

# Kill any existing server on port 5000
fuser -k 5000/tcp 2>/dev/null

# Start server
echo "[3/4] Starting server on port 5000..."
python3 app.py &
sleep 3

echo "[4/4] Server running!"
echo ""
echo "  Local:    http://localhost:5000"
echo "  Admin:    admin@bridgestonecapital.com / admin123"
echo "  Borrower: borrower@demo.com / demo123"
echo "  Investor: investor@demo.com / demo123"
echo ""
echo "  Press Ctrl+C to stop"
echo "======================================="

# Keep running
wait
