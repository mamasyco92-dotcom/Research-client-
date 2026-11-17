@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYEXE="
if exist "%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe" set "PYEXE=%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe"
if not defined PYEXE (
  where py >nul 2>&1
  if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
  where python >nul 2>&1
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo Python introuvable. Ouvrir le dossier dans VS Code et selectionner l'interpreteur Python.
  pause
  exit /b 1
)
echo Installation/verifications des dependances...
%PYEXE% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Erreur d'installation : verifier la version Python ou la connexion Internet.
  pause
  exit /b 1
)
echo Lancement de l'application locale...
%PYEXE% -m streamlit run app.py --server.address 127.0.0.1
pause
