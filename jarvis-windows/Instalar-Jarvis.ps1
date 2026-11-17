# Instalador de Jarvis para Windows.
# Ejecutar con clic derecho -> "Ejecutar con PowerShell" (o con Instalar.bat).
$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
Write-Host "=== Instalando Jarvis ===" -ForegroundColor Cyan

# 1. Python 3.12
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Instalando Python 3.12 (puede pedir permiso)..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}

# 2. Entorno virtual y dependencias
if (-not (Test-Path ".venv")) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Clave de Anthropic y nombre
if (-not [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY","User")) {
    Write-Host ""
    Write-Host "Necesito tu clave de API de Anthropic (se crea en platform.claude.com, en la sección API Keys)."
    $key = Read-Host "Pega tu clave y pulsa Enter" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($key))
    [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $plain, "User")
}
if (-not [Environment]::GetEnvironmentVariable("JARVIS_NOMBRE_USUARIO","User")) {
    $name = Read-Host "¿Cómo quieres que te llame Jarvis?"
    [Environment]::SetEnvironmentVariable("JARVIS_NOMBRE_USUARIO", $name, "User")
}

# 4. Descargar modelos de voz ahora (para que el primer arranque sea rápido)
Write-Host "Descargando modelos de voz..."
& .\.venv\Scripts\python.exe -c "import openwakeword; openwakeword.utils.download_models(['hey_jarvis']); from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# 5. Acceso directo en el escritorio
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\Jarvis.lnk")
$lnk.TargetPath = "$Dir\.venv\Scripts\pythonw.exe"
$lnk.Arguments = "`"$Dir\jarvis.py`""
$lnk.WorkingDirectory = $Dir
$lnk.IconLocation = "shell32.dll,138"
$lnk.Save()

Write-Host ""
Write-Host "Listo. Abre 'Jarvis' desde el escritorio y di 'Hey Jarvis'." -ForegroundColor Green
Read-Host "Pulsa Enter para cerrar"
