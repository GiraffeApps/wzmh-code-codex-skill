@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "CodeHealthScript=%~dp0health_check.py"
set "CodeJson="
set "CodeEnsure="
:parse
if "%~1"=="" goto launch
if "%~1"=="--json" goto json_argument
if "%~1"=="--ensure-ready" goto ensure_argument
goto usage
:json_argument
set "CodeJson=1"
shift
goto parse
:ensure_argument
set "CodeEnsure=1"
shift
goto parse
:launch

set "CodeLocalData=%LOCALAPPDATA%"
if not defined CodeLocalData set "CodeLocalData=%USERPROFILE%\AppData\Local"
set "CodePythonFailure=selected_interpreter_unavailable"
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
for %%I in ("%CodePython%") do set "CodePythonSize=%%~zI"
if "%CodePythonSize%"=="0" goto missing_python
if not exist "%CodeHealthScript%" goto missing_helper
"%CodePython%" -B "%CodeHealthScript%" %*
exit /b %errorlevel%

:missing_python
if not exist "%CodeHealthScript%" goto missing_helper
rem Read-only bounded discovery; no settings, installs, PATH edits or automatic fallback.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$ErrorActionPreference='SilentlyContinue'; $found=@(); $watch=[Diagnostics.Stopwatch]::StartNew(); function AddCandidate($p,$origin) { if($script:found.Count -ge 16 -or -not $p -or -not [IO.Path]::IsPathRooted($p)){return}; try {$f=Get-Item -LiteralPath $p -ErrorAction Stop; if($f.PSIsContainer -or $f.Length -le 0 -or $script:found.path -contains $f.FullName){return}; $script:found+=@{path=$f.FullName;origin=$origin}}catch{} }; function RunLocal($file,$arguments,$milliseconds) { $proc=New-Object Diagnostics.Process; try { $proc.StartInfo=New-Object Diagnostics.ProcessStartInfo; $proc.StartInfo.FileName=$file;$proc.StartInfo.Arguments=$arguments; $proc.StartInfo.UseShellExecute=$false;$proc.StartInfo.CreateNoWindow=$true; $proc.StartInfo.RedirectStandardOutput=$true;$proc.StartInfo.RedirectStandardError=$true; $null=$proc.Start();$out=$proc.StandardOutput.ReadToEndAsync();$err=$proc.StandardError.ReadToEndAsync(); if(-not $proc.WaitForExit($milliseconds)){$proc.Kill();return}; if(-not $out.Wait(100)){return}; return @{code=$proc.ExitCode;text=$out.Result} }catch{}finally{$proc.Dispose()} }; AddCandidate $env:CodePython 'selected location'; AddCandidate ($env:CodeLocalData+'\WZMH\OBCReference\python\Scripts\python.exe') 'managed location'; AddCandidate ($env:CodeLocalData+'\Python\pythoncore-3.14-64\python.exe') 'supported per-user location'; foreach($command in @(Get-Command python.exe,python3.exe -CommandType Application -All -ErrorAction SilentlyContinue)){AddCandidate $command.Source 'PATH'}; foreach($r in @(Get-ItemProperty -Path 'Registry::HKEY_CURRENT_USER\Software\Python\*\*\InstallPath','Registry::HKEY_LOCAL_MACHINE\Software\Python\*\*\InstallPath','Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python\*\*\InstallPath' -ErrorAction SilentlyContinue)) { if($r.ExecutablePath){AddCandidate $r.ExecutablePath 'Python registration'} elseif($r.PSParentPath -like '*\PythonCore\*' -and $r.'(default)'){AddCandidate ($r.'(default)'+'\python.exe') 'PythonCore registration'} }; foreach($pattern in @(($env:CodeLocalData+'\Programs\Python\Python*\python.exe'),($env:CodeLocalData+'\Python\pythoncore-*\python.exe'),($env:ProgramFiles+'\Python*\python.exe'),(${env:ProgramFiles(x86)}+'\Python*\python.exe'),($env:SystemDrive+'\Python*\python.exe'),($env:USERPROFILE+'\miniconda3\python.exe'),($env:USERPROFILE+'\anaconda3\python.exe'))) { foreach($f in @(Get-Item -Path $pattern -ErrorAction SilentlyContinue)){AddCandidate $f.FullName 'standard installation location'} }; foreach($launcher in @(Get-Command py.exe -CommandType Application -All -ErrorAction SilentlyContinue)) { if($watch.ElapsedMilliseconds -gt 6500){break}; $file=Get-Item -LiteralPath $launcher.Source -ErrorAction SilentlyContinue; if(-not $file -or $file.Length -le 0){continue}; $listing=RunLocal $file.FullName '-0p' 1500; if($listing -and $listing.code -eq 0){ foreach($m in [regex]::Matches($listing.text,'(?im)(?:[A-Z]:[\\/]|\\\\)[^\r\n]*?python(?:3(?:\.\d+)?)?\.exe')){AddCandidate $m.Value 'Python launcher list'} };break }; $env:WZMH_HEALTH_CANDIDATES=ConvertTo-Json -InputObject @($found) -Depth 4 -Compress; foreach($candidate in $found) { if($watch.ElapsedMilliseconds -gt 8000){break}; $probe=RunLocal $candidate.path '-I -B -c __import__(''sys'').exit(73*((__import__(''sys'').implementation.name==''cpython'')&(__import__(''sys'').version_info>=(3,8))))' 1500; if($probe -and $probe.code -eq 73){ $a=@();if($env:CodeJson){$a+='--json'}; & $candidate.path -B $env:CodeHealthScript --diagnose-python @a;exit $LASTEXITCODE } }; $detail='No usable Python interpreter was verified in the locations checked. This does not prove Python is not installed.'; $fix='Ask IT for an existing CPython executable path or deploy the required environment, then configure WZMH_CODE_PYTHON. Custom or unregistered installations may not be discovered.'; $r=@{ok=$false;reason='python_not_verified';checks=@(@{name='Python';status='FAIL';detail=$detail;fix=$fix});python_configuration=@{selected_path=$env:CodePython;explicit_setting=$env:WZMH_CODE_PYTHON};candidate_paths=@($found);detected_pythons=@();configuration_changed=$false;remaining_health_checks_run=$false;elapsed_ms=$watch.ElapsedMilliseconds}; if($env:CodeJson){$r|ConvertTo-Json -Depth 6 -Compress}else{Write-Output ('FAIL  Python: '+$detail);Write-Output ('      Skill selected: '+$env:CodePython);Write-Output ('      Fix: '+$fix)}; exit 1"
exit /b %errorlevel%

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
echo Usage: health_check.cmd [--json] [--ensure-ready]
exit /b 2
