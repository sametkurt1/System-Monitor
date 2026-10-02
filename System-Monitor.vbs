Set oShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set shApp = CreateObject("Shell.Application")

strDir = fso.GetParentFolderName(WScript.ScriptFullName)
strSelf = WScript.ScriptFullName
oShell.CurrentDirectory = strDir

' Locate pythonw.exe: prefer the one next to the running interpreter, fall back to PATH
Function FindPythonw()
    Dim candidates, i, p
    candidates = Array( _
        oShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
        "pythonw.exe")
    For i = 0 To UBound(candidates)
        p = candidates(i)
        If InStr(p, "\") > 0 Then
            If fso.FileExists(p) Then
                FindPythonw = p
                Exit Function
            End If
        Else
            Dim resolved
            On Error Resume Next
            resolved = oShell.RegRead("HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\CurrentWindowedExecutable")
            On Error GoTo 0
            If Err.Number = 0 And Len(resolved) > 0 Then
                If fso.FileExists(resolved) Then
                    FindPythonw = resolved
                    Exit Function
                End If
            End If
            FindPythonw = p
            Exit Function
        End If
    Next
    FindPythonw = "pythonw.exe"
End Function

strPythonw = FindPythonw()
strArgs = """" & strSelf & """"

' Keep the desktop shortcut in sync with this script's location, so moving the
' folder never leaves a broken shortcut behind.  The shortcut goes through this
' script (not straight to pythonw) so a double-click always ends up elevated.
strDesktop = oShell.SpecialFolders("Desktop")
strLnk = strDesktop & "\System Monitor.lnk"

Set oLink = oShell.CreateShortcut(strLnk)
oLink.TargetPath = oShell.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
oLink.Arguments = strArgs
oLink.WorkingDirectory = strDir
If fso.FileExists(strDir & "\icon.ico") Then
    oLink.IconLocation = strDir & "\icon.ico"
End If
oLink.Description = "System Monitor - CPU / RAM / GPU Desktop Monitor (runs as Administrator)"
oLink.Save

' Launch without a console window and with Administrator rights.  "runas" makes
' UAC appear once, here, instead of inside Python.
shApp.ShellExecute strPythonw, """" & strDir & "\sysmon.py""", strDir, "runas", 0