@echo off
cd /d %~dp0
python runtime\runner.py --mode ccswitch --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 1 --effort low
pause
