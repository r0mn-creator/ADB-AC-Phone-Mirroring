' ═══════════════════════════════════════════════════════════════════════════
'  Phone Mirror — Auto-Start Minimized (start_minimized.vbs)
' ═══════════════════════════════════════════════════════════════════════════
'  Launches server.py in a minimized window so it runs in the background.
'
'  HOW TO USE WITH STEAM (auto-launch when AC starts):
'    1. Right-click Assetto Corsa in Steam → Properties → Launch Options
'    2. Paste this (fix the path to match your folder):
'       wscript "C:\PhoneMirror\start_minimized.vbs" && %command%
'    3. Every time you launch AC, the server starts automatically
'
'  HOW TO USE WITH WINDOWS STARTUP (starts on login):
'    1. Press Win+R → type: shell:startup → press Enter
'    2. Create a shortcut to this .vbs file in that folder
'
'  TO STOP: Task Manager → find python.exe → End Task
'
'  Goes in: Same folder as server.py
' ═══════════════════════════════════════════════════════════════════════════

' Get the folder where this script lives (same folder as server.py)
Dim scriptDir
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Build command: change to script folder, then run server.py
Dim cmd
cmd = "cmd /c cd /d """ & scriptDir & """ && python server.py"

' Launch minimized (7 = minimized window, False = don't wait for it to finish)
CreateObject("WScript.Shell").Run cmd, 7, False
