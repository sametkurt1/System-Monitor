Set oShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strDir = fso.GetParentFolderName(WScript.ScriptFullName)
oShell.CurrentDirectory = strDir

' Ensure desktop shortcut exists with custom icon
strLnk = strDir & "\System Monitor.lnk"
If Not fso.FileExists(strLnk) Then
    Set oLink = oShell.CreateShortcut(strLnk)
    oLink.TargetPath = "pythonw.exe"
    oLink.Arguments = """" & strDir & "\sysmon.py"""
    oLink.WorkingDirectory = strDir
    If fso.FileExists(strDir & "\icon.ico") Then
        oLink.IconLocation = strDir & "\icon.ico"
    End If
    oLink.Description = "System Monitor"
    oLink.Save
End If

' Launch without console window
oShell.Run "pythonw """ & strDir & "\sysmon.py""", 0, False
