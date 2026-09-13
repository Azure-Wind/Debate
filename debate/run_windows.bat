@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   双AI高真实性辩论攻防模拟器 v5.1
 echo   Windows CMD 启动器
 echo ==========================================
echo.

echo 当前目录：%CD%
echo.

if "%1"=="" goto MENU
if /I "%1"=="mock" goto MOCK
if /I "%1"=="lmstudio" goto LMSTUDIO
if /I "%1"=="ccswitch" goto CCSWITCH
if /I "%1"=="doctor" goto DOCTOR
if /I "%1"=="dry" goto DRY

echo 用法：
echo   run_windows.bat mock
 echo   run_windows.bat lmstudio
 echo   run_windows.bat ccswitch
 echo   run_windows.bat doctor
 echo   run_windows.bat dry
 goto END

:MENU
echo 1. 完全离线 Mock 测试
 echo 2. 本机 LM Studio
 echo 3. CCSwitch + DeepSeek
 echo 4. 环境检查
 echo 5. Dry-run（只生成隔离上下文）
echo.
choice /C 12345 /N /M "请选择 [1-5]: "
if errorlevel 5 goto DRY
if errorlevel 4 goto DOCTOR
if errorlevel 3 goto CCSWITCH
if errorlevel 2 goto LMSTUDIO
if errorlevel 1 goto MOCK

:MOCK
python runtime\runner.py --mode mock --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 3
 goto END

:LMSTUDIO
python runtime\runner.py --mode lmstudio --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 3
 goto END

:CCSWITCH
python runtime\runner.py --mode ccswitch --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 3
 goto END

:DOCTOR
python runtime\doctor.py
 goto END

:DRY
python runtime\runner.py --mode mock --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 1 --dry-run
 goto END

:END
echo.
pause
