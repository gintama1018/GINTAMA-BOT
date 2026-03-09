#!/usr/bin/env python3
"""
TCC — Terminal Command Center
Codename: JARVIS v2.0

Entry point. Run this to start the JARVIS terminal.
Usage:
    python main.py
    python main.py --agent   (start the device agent instead)
"""

import sys
import os

# Load .env file before anything else — sets GEMINI_API_KEY etc.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # dotenv optional — env vars can be set manually too

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if "--agent" in sys.argv:
        # Start the device agent (for remote devices)
        from agent.agent import start_agent
        start_agent()
    else:
        # Start the interactive JARVIS terminal
        from src.cli import TCC_CLI
        cli = TCC_CLI()
        cli.run()


if __name__ == "__main__":
    main()
