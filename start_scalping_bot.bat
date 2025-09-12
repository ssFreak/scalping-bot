@echo off
REM Se schimbă directorul curent în cel unde se află fișierul .bat
cd /d %~dp0

REM Rulează scriptul ca modul, ca Python să găsească folderele core și strategies
python -m bot_manager

pause