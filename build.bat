@echo off
setlocal EnableDelayedExpansion
title DocCipher Breaker Build System
color 0C

cd /d "%~dp0"

echo.
echo  +===========================================+
echo  ^|   DocCipher Breaker Build System          ^|
echo  ^|   Created by Achu Vijayakumar             ^|
echo  ^|   FOR EDUCATIONAL PURPOSES ONLY           ^|
echo  +===========================================+
echo.

:: ------------------------------------------------------------- toolchain

python --version >nul 2>&1
if errorlevel 1 (
    echo  [x] Python is not installed or not on PATH.
    echo      Install Python 3.10 or newer from https://python.org
    goto :fail
)
for /f "delims=" %%V in ('python --version 2^>^&1') do echo  Using %%V

:: ------------------------------------------------------------ delegate

:: The real pipeline lives in build.ps1 -- it runs the tests, regenerates the
:: branding assets from logo.svg, builds via DocCipherBreaker.spec (which
:: bundles backend/static correctly), and compiles the Inno Setup installer.
:: Duplicating a second PyInstaller command here would drift out of sync.

echo.
echo  Running build pipeline...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "build.ps1" %*
if errorlevel 1 (
    echo.
    echo  [x] Build failed. See the output above.
    goto :fail
)

if not exist "dist\DocCipherBreaker.exe" (
    echo  [x] Expected dist\DocCipherBreaker.exe was not produced.
    goto :fail
)

:: --------------------------------------------------- distribution bundle

:: Read the version from the manifest so the versioned installer can be found.
for /f "usebackq tokens=1,* delims==" %%A in ("version.txt") do (
    if /I "%%A"=="version" set "APPVER=%%B"
)

echo.
echo  Assembling distribution bundle...

if exist "release" rmdir /s /q "release"
mkdir "release"

copy /y "dist\DocCipherBreaker.exe" "release\" >nul
copy /y "README.md"                 "release\" >nul
copy /y "LICENSE"                   "release\" >nul
copy /y "update.bat"                "release\" >nul
copy /y "version.txt"               "release\" >nul

:: The updater fetches the installer from /releases/latest/download/ by an
:: exact filename, so a copy with a stable, unversioned name is published
:: alongside the versioned one.
if exist "dist_installer\DocCipherBreaker_Setup_%APPVER%.exe" (
    copy /y "dist_installer\DocCipherBreaker_Setup_%APPVER%.exe" ^
            "release\DocCipherBreaker_Setup.exe" >nul
)

:: Stamp the bundle's own checksum so whoever publishes it can paste the hash
:: straight into the server-side manifest that update.bat verifies against.
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command ^
  "(Get-FileHash -Algorithm SHA256 'release\DocCipherBreaker.exe').Hash"`) do set "EXEHASH=%%H"

echo %EXEHASH%  DocCipherBreaker.exe> "release\SHA256SUMS.txt"

if exist "release\DocCipherBreaker_Setup.exe" (
    for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command ^
      "(Get-FileHash -Algorithm SHA256 'release\DocCipherBreaker_Setup.exe').Hash"`) do set "SETUPHASH=%%H"
)
if defined SETUPHASH echo %SETUPHASH%  DocCipherBreaker_Setup.exe>> "release\SHA256SUMS.txt"

echo.
echo  +===========================================+
echo  ^|   [OK] Build successful                   ^|
echo  +===========================================+
echo.
echo    Output: release\
echo      - DocCipherBreaker.exe
echo      - README.md
echo      - LICENSE
echo      - update.bat
echo      - version.txt
echo      - DocCipherBreaker_Setup.exe
echo      - SHA256SUMS.txt
echo.
echo    Installer: dist_installer\
echo.
echo    SHA-256 of the executable:
echo    %EXEHASH%
if defined SETUPHASH (
    echo.
    echo    SHA-256 of the installer ^(publish as installer_sha256^):
    echo    %SETUPHASH%
)
echo.
echo    Publish that hash as the sha256= line in the version.txt you host,
echo    or update.bat will refuse to install the download.
echo.
echo    Created by Achu Vijayakumar
echo    FOR EDUCATIONAL PURPOSES ONLY
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
