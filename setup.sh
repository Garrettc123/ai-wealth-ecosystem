#!/bin/bash

echo "Setting up AI Wealth Ecosystem..."

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
mkdir -p data logs reports

# Copy config template
if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "Config template created. Please edit config.json with your settings."
fi

echo "Setup complete!"
echo "To start: python wealth_generator.py"
