@echo off
setlocal EnableDelayedExpansion
title DocCipher Breaker Updater
color 0A

set "APP=DocCipherBreaker.exe"
set "HERE=%~dp0"
cd /d "%HERE%"

echo.
echo  +===========================================+
echo  ^|   DocCipher Breaker Updater               ^|
echo  ^|   Created by Achu Vijayakumar             ^|
echo  ^|   FOR EDUCATIONAL PURPOSES ONLY           ^|
echo  +===========================================+
echo.

:: ---------------------------------------------------------------- checks

if not exist "version.txt" (
    echo  [x] version.txt not found next to this script.
    echo      Run this updater from your DocCipher Breaker folder.
    goto :fail
)

where powershell >nul 2>&1
if errorlevel 1 (
    echo  [x] PowerShell not found. Cannot download updates.
    goto :fail
)

:: Read the local manifest.
call :readkey "version.txt" version LOCAL_VER
call :readkey "version.txt" update_url UPDATE_URL
if "%LOCAL_VER%"=="" set "LOCAL_VER=0.0.0"

echo  Installed version : %LOCAL_VER%
echo  Update source     : %UPDATE_URL%
echo.

echo %UPDATE_URL% | findstr /I /B /C:"https://" >nul
if errorlevel 1 (
    echo  [x] The update URL is not HTTPS. Refusing to continue.
    echo      An update fetched over plain HTTP can be replaced in transit.
    goto :fail
)

:: ------------------------------------------------------- remote manifest

echo  Checking for updates...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%UPDATE_URL%' -OutFile 'remote_version.txt' -UseBasicParsing -TimeoutSec 30; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo  [x] Could not reach the update server.
    echo      Check your internet connection, or the update_url in version.txt.
    goto :fail
)

call :readkey "remote_version.txt" version REMOTE_VER
call :readkey "remote_version.txt" download_url DOWNLOAD_URL
call :readkey "remote_version.txt" sha256 EXPECTED_HASH

if "%REMOTE_VER%"=="" (
    echo  [x] The update server returned a manifest with no version field.
    goto :cleanfail
)

echo  Latest version    : %REMOTE_VER%
echo.

if /I "%REMOTE_VER%"=="%LOCAL_VER%" (
    echo  [OK] You already have the latest version.
    del /q remote_version.txt >nul 2>&1
    goto :done
)

:: A hash is mandatory. Without it we cannot tell a real update from a
:: substituted binary, and this script would become a way to run arbitrary
:: code on this machine.
if "%EXPECTED_HASH%"=="" (
    echo  [x] The update manifest carries no sha256 checksum.
    echo      Refusing to install an unverified executable.
    goto :cleanfail
)

echo %DOWNLOAD_URL% | findstr /I /B /C:"https://" >nul
if errorlevel 1 (
    echo  [x] The download URL is not HTTPS. Refusing to continue.
    goto :cleanfail
)

echo  Update available: %LOCAL_VER% -^> %REMOTE_VER%
set /p "GO=  Install it now? [y/N] "
if /I not "%GO%"=="y" (
    echo  Cancelled. Nothing was changed.
    del /q remote_version.txt >nul 2>&1
    goto :done
)

:: ------------------------------------------------------------- download

echo.
echo  Downloading...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile 'DocCipherBreaker.new' -UseBasicParsing -TimeoutSec 300; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo  [x] Download failed. Your current version is untouched.
    goto :cleanfail
)

:: ------------------------------------------------------------- verify

echo  Verifying checksum...
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command ^
  "(Get-FileHash -Algorithm SHA256 'DocCipherBreaker.new').Hash"`) do set "ACTUAL_HASH=%%H"

echo    expected : %EXPECTED_HASH%
echo    actual   : %ACTUAL_HASH%

if /I not "%ACTUAL_HASH%"=="%EXPECTED_HASH%" (
    echo.
    echo  [x] CHECKSUM MISMATCH -- the downloaded file is NOT what the
    echo      publisher signed. It may have been corrupted in transit or
    echo      replaced by someone else. It will be deleted and NOT installed.
    del /q DocCipherBreaker.new >nul 2>&1
    goto :cleanfail
)
echo  [OK] Checksum verified.

:: -------------------------------------------------------------- install

echo  Closing the application if it is running...
taskkill /f /im "%APP%" >nul 2>&1
:: Give Windows a moment to release the file lock.
ping -n 3 127.0.0.1 >nul

if exist "DocCipherBreaker_old.exe" del /q "DocCipherBreaker_old.exe" >nul 2>&1
if exist "%APP%" (
    ren "%APP%" "DocCipherBreaker_old.exe"
    if errorlevel 1 (
        echo  [x] Could not replace the running executable.
        echo      Close DocCipher Breaker and run this updater again.
        del /q DocCipherBreaker.new >nul 2>&1
        goto :cleanfail
    )
    echo  [OK] Previous version kept as DocCipherBreaker_old.exe
)

ren "DocCipherBreaker.new" "%APP%"
if errorlevel 1 (
    echo  [x] Could not install the update. Rolling back...
    if exist "DocCipherBreaker_old.exe" ren "DocCipherBreaker_old.exe" "%APP%"
    goto :cleanfail
)

move /y remote_version.txt version.txt >nul 2>&1

echo.
echo  +===========================================+
echo  ^|   [OK] Update installed                   ^|
echo  +===========================================+
echo    Version : %REMOTE_VER%
echo    Date    : %DATE%
echo.
echo    Created by Achu Vijayakumar
echo    FOR EDUCATIONAL PURPOSES ONLY
echo.
echo    The previous version is kept as DocCipherBreaker_old.exe
echo    in case you need to roll back.
echo.

set /p "LAUNCH=  Launch DocCipher Breaker now? [Y/n] "
if /I not "%LAUNCH%"=="n" start "" "%APP%"
goto :done

:: ------------------------------------------------------------- helpers

:readkey
:: %1 = file, %2 = key, %3 = variable to set. Ignores # comment lines.
set "%~3="
for /f "usebackq tokens=1,* delims==" %%A in ("%~1") do (
    set "K=%%A"
    if /I "!K!"=="%~2" set "%~3=%%B"
)
exit /b 0

:cleanfail
del /q remote_version.txt >nul 2>&1

:fail
echo.
echo  Update aborted. Your installation was not changed.
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0
