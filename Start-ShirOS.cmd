@echo off
cd /d "%~dp0"
uv run python scripts/launch_workbench.py
if errorlevel 1 pause
