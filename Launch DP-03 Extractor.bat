@echo off
setlocal EnableDelayedExpansion
rem ==========================================================================
rem  DP-03 Extractor — Windows launcher
rem
rem  Finds a usable Python (with Tkinter), makes sure numpy + sounddevice
rem  are installed for the optional live mixer, then runs the app. The
rem  window stays open if anything fails so you can read the error.
rem ==========================================================================

pushd "%~dp0"

rem --- candidate interpreters, in order of preference -----------------------
set "CANDIDATES=py -3.12;py -3.11;py -3.10;py -3;python;python3"

set "PY="
for %%C in ("%CANDIDATES:;=" "%") do (
    if not defined PY (
        rem Strip surrounding quotes from the candidate.
        set "cand=%%~C"
        rem Try the candidate — probe for a working Tk.
        call :PROBE_PY "!cand!"
        if not errorlevel 1 (
            set "PY=!cand!"
        )
    )
)

if not defined PY (
    echo.
    echo  ERROR: No Python with Tkinter was found.
    echo.
    echo  Install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/windows/
    echo.
    echo  During install, keep the default "tcl/tk and IDLE" option checked
    echo  and tick "Add python.exe to PATH". Then re-run this launcher.
    echo.
    pause
    popd
    exit /b 1
)

echo Using python: %PY%

rem --- make sure the optional mixer deps are present ------------------------
%PY% -c "import numpy, sounddevice" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Installing optional mixer dependencies ^(numpy, sounddevice^)...
    %PY% -m pip install --user numpy sounddevice
    if errorlevel 1 (
        echo.
        echo  WARNING: Could not install numpy / sounddevice. The extractor
        echo  will still run, but the live mixer will be disabled.
        echo.
    )
)

rem --- launch ---------------------------------------------------------------
%PY% -m dp03app
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo  The app exited with code %RC%.
    pause
)

popd
exit /b %RC%


rem ==========================================================================
rem  :PROBE_PY  — returns 0 if %1 is a python interpreter with working Tk.
rem ==========================================================================
:PROBE_PY
set "cand=%~1"
%cand% -c "import tkinter; r=tkinter.Tk(); r.withdraw(); r.destroy()" >nul 2>&1
exit /b %ERRORLEVEL%
