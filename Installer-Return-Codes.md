# 🧩 Linkium Installer Return Codes

This document lists all possible **return (exit) codes** that the **Linkium EXE installer** may produce during installation or uninstallation.  
These codes are based on **Inno Setup’s standard return codes** and common Windows error codes.

---

## 📘 Overview
When the Linkium installer finishes running, it returns a **numeric exit code**.  
These codes help automated systems (like Microsoft Store, CI/CD pipelines, or deployment tools) determine if the installation was successful or encountered an issue.

---

## ✅ Standard Return Codes

| **Scenario** | **Return Code** | **Description** |
|---------------|----------------|-----------------|
| **Installation successful** | `0` | The installation completed successfully. |
| **Installation cancelled by user** | `1` | The user cancelled the setup before completion. |
| **Application already exists** | `2` | A previous version of Linkium is already installed. |
| **Installation already in progress** | `125` | Another installation is currently in progress. |
| **Disk space is full** | `112` | The target drive does not have enough free disk space. |
| **Reboot required** | `3010` | A system restart is required to complete the installation. |
| **Network failure** | `12007` | Network issue or failed download during setup. |
| **Package rejected during installation** | `5` | Access denied or blocked by system security policy. |
| **Miscellaneous install failure** | `1603` | A general installation error occurred. |

---

## ⚙️ How to Use These Codes

### 🔸 For Microsoft Store Submission
Use the table above in the **Installer Handling** section:
- **Installation successful →** `0`
- **Installation cancelled by user →** `1`
- **Disk space full →** `112`
- etc.

### 🔸 For Developers
You can capture the exit code in a script or deployment system like this:

```powershell
Start-Process -FilePath "Linkium.exe" -ArgumentList "/VERYSILENT /NORESTART" -Wait
Write-Host "Exit code:" $LASTEXITCODE
