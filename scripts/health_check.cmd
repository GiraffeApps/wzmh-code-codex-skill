@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "CodeJson="
if "%~1"=="--json" set "CodeJson=1"
if not "%~1"=="" if not defined CodeJson goto usage
if not "%~2"=="" goto usage

set "CodeLocalData=%LOCALAPPDATA%"
if not defined CodeLocalData set "CodeLocalData=%USERPROFILE%\AppData\Local"
set "CodePython=%WZMH_CODE_PYTHON%"
if defined CodePython goto validate
set "CodePython=%CodeLocalData%\WZMH\OBCReference\python\Scripts\python.exe"
if exist "%CodePython%" goto validate
set "CodePython=%CodeLocalData%\Python\pythoncore-3.14-64\python.exe"

:validate
if "%CodePython:~0,2%"=="\\" goto check_file
if "%CodePython:~1,2%"==":\" goto check_file
if "%CodePython:~1,2%"==":/" goto check_file
goto missing_python

:check_file
if not exist "%CodePython%" goto missing_python
for %%I in ("%CodePython%") do set "CodePythonAttributes=%%~aI"
if "%CodePythonAttributes:~0,1%"=="d" goto missing_python
if not exist "%~dp0health_check.py" goto missing_helper
"%CodePython%" -B "%~dp0health_check.py" %*
exit /b %errorlevel%

:missing_python
if defined CodeJson goto missing_python_json
echo FAIL  Python: The configured Python executable was not found or is not an absolute file path.
echo       Fix: Ask IT to deploy Python or set WZMH_CODE_PYTHON to its absolute python.exe path. No fallback install was attempted.
echo Setup check failed.
exit /b 1
:missing_python_json
echo {"ok":false,"checks":[{"name":"Python","status":"FAIL","detail":"Configured Python was not found or is not an absolute file path.","fix":"Ask IT to deploy Python or set WZMH_CODE_PYTHON to its absolute python.exe path. No fallback install was attempted."}],"elapsed_ms":0}
exit /b 1

:missing_helper
if defined CodeJson goto missing_helper_json
echo FAIL  Skill files: The health-check helper is missing.
echo       Fix: Restore scripts/health_check.py from the approved skill package.
echo Setup check failed.
exit /b 1
:missing_helper_json
echo {"ok":false,"checks":[{"name":"Skill files","status":"FAIL","detail":"The health-check helper is missing.","fix":"Restore scripts/health_check.py from the approved skill package."}],"elapsed_ms":0}
exit /b 1

:usage
echo Usage: health_check.cmd [--json]
exit /b 2
