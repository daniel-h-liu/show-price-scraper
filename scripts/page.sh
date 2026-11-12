#!/bin/bash
# Open the tracker page (http://localhost:8765) to pick shows and browse prices. Ctrl-C to stop it.
cd "$(dirname "$0")/.." && exec .venv/bin/python -m scraper.web
