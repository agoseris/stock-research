@echo off
wsl.exe -d Ubuntu -- bash -c "/home/agoseris/projects/stock-research/scripts/venv/bin/python3 /home/agoseris/projects/stock-research/scripts/auto_ingest.py >> /home/agoseris/projects/stock-research/scripts/auto_ingest.log 2>&1"
