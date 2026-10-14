' Hidden launcher for scheduled ShirOS runtime actions.
' Runs powershell.exe with a hidden window and waits, so the Task Scheduler
' result reflects the script exit code without flashing a console window.
Option Explicit
Dim shell, script, action, command, code
If WScript.Arguments.Count < 1 Then WScript.Quit 2
script = WScript.Arguments(0)
action = ""
If WScript.Arguments.Count > 1 Then action = WScript.Arguments(1)
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
If Len(action) > 0 Then command = command & " " & action
Set shell = CreateObject("WScript.Shell")
code = shell.Run(command, 0, True)
WScript.Quit code
