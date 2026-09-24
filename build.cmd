@echo off
REM Build ReviewTrans Studio tren may ca nhan: ra installer + zip portable trong thu muc release\
REM Vi du:  build.cmd                      (ban day du)
REM         build.cmd --version 2.1.0
REM         build.cmd --no-libmpv --skip-installer
REM Can: Python 3.10+ (co "py" hoac "python" trong PATH). Installer can Inno Setup 6.
setlocal
cd /d "%~dp0"
set "VENV_DIR=.venv_build"
set "PY=%VENV_DIR%\Scripts\python.exe"

REM venv hong (vd. Python goc bi go) thi tao lai
if exist "%PY%" (
  "%PY%" -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo [build] venv build bi hong, tao lai...
    rmdir /s /q "%VENV_DIR%"
  )
)
if not exist "%PY%" (
  echo [build] Tao venv %VENV_DIR% ...
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv "%VENV_DIR%"
  ) else (
    python -m venv "%VENV_DIR%"
  )
  if not exist "%PY%" (
    echo [build] Khong tao duoc venv. Hay cai Python 3.10+ va them vao PATH.
    exit /b 1
  )
)

echo [build] Cai thu vien...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if errorlevel 1 exit /b 1

"%PY%" scripts\build.py all %*
if errorlevel 1 (
  echo [build] THAT BAI
  exit /b 1
)
echo.
echo [build] Xong. Ket qua trong thu muc release\
dir /b release
endlocal
