#!/usr/bin/env bash
# KSA EV Tender Monitor — Setup Script
# Run once to install dependencies and configure the environment.

set -euo pipefail

echo "=== KSA EV Tender Monitor Setup ==="

# Check Python version
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: Python 3.13+ is required but not found."
    exit 1
fi

echo "Using: $PYTHON_CMD ($($PYTHON_CMD --version))"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
fi

# Activate venv
if [ -f "venv/Scripts/activate" ]; then
    # Windows (Git Bash / MSYS)
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# Install dependencies
echo "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browsers
echo "Installing Playwright Chromium browser..."
playwright install chromium

# Create .env from example if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "IMPORTANT: Edit .env with your Gmail App Password and recipient emails."
    echo "  File: $(pwd)/.env"
fi

# Create output directory
mkdir -p output

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Gmail credentials"
echo "  2. Test run:  python main.py --no-email"
echo "  3. Full run:  python main.py"
echo ""
echo "Cron (Linux/Mac — run daily at 8 AM KSA / 5 AM UTC):"
echo "  0 5 * * * cd $(pwd) && venv/bin/python main.py >> output/cron.log 2>&1"
echo ""
echo "Task Scheduler (Windows):"
echo "  Action: $(pwd)/venv/Scripts/python.exe"
echo "  Arguments: main.py"
echo "  Start in: $(pwd)"
echo "  Trigger: Daily at 08:00"
