# Quick Start Guide - AI Wealth Ecosystem

## Get Running in 5 Minutes

### Option 1: Local Setup

```bash
# Clone repository
git clone https://github.com/Garrettc123/ai-wealth-ecosystem.git
cd ai-wealth-ecosystem

# Run setup
chmod +x setup.sh
./setup.sh

# Configure settings
cp config.example.json config.json
# Edit config.json with your API keys

# Start system
python wealth_generator.py
```

### Option 2: Docker

```bash
docker build -t ai-wealth-ecosystem .
docker run -v $(pwd)/config.json:/app/config.json ai-wealth-ecosystem
```

### Option 3: Termux (Android)

```bash
pkg install python git
git clone https://github.com/Garrettc123/ai-wealth-ecosystem.git
cd ai-wealth-ecosystem
pip install -r requirements.txt
python wealth_generator.py
```

## First Run

On first run, you'll see:

```
============================================================
Autonomous AI Wealth Generation Ecosystem
============================================================
Starting at: 2025-11-17 19:55:00
Monthly target: $3000.00
============================================================

Initializing income streams...
Initialized: API Monetization
Initialized: Crypto Arbitrage
Initialized: Content Generation
Initialized: Bounty Hunting
Initialized: Affiliate Marketing

--- Cycle 1 ---
Processing API Monetization...
Processing Crypto Arbitrage...
...
```

## Monitor Progress

```bash
# Real-time dashboard
python monitor.py

# Check earnings
cat wealth_report.json
```

## Troubleshooting

**No income generated?**
- Check config.json has valid API keys
- Ensure internet connection
- Review logs/

**Import errors?**
```bash
pip install -r requirements.txt
```

## Next Steps

1. Configure real API keys in config.json
2. Customize income targets
3. Enable desired strategies
4. Monitor performance
5. Scale to cloud for 24/7 operation

## Support

Issues? Check the main README or open a GitHub issue.
