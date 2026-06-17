# SignalLoomOps Windows Installer

Run from the project root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\build_win.ps1
```

Outputs:

- Portable app: `dist\SignalLoomOps\SignalLoomOps.exe`
- Wizard installer: `dist\installer\SignalLoomOps_Setup.exe`

If Inno Setup is not installed, install Inno Setup 6 and rerun the build. The build script also checks for an optional local installer at:

```text
installer\innosetup-6.7.3.exe
```

Windows SmartScreen may warn because the installer is unsigned. Code signing requires a separate certificate.
