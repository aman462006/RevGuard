# Start RevGuard locally in PRODUCTION mode with afrec recording enabled.
# Everything is set in THIS process, so nothing leaks into other shells.
#
# Run it from the repo root (cmd or PowerShell):
#
#     powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
#
# It prompts for the Groq key once if GROQ_API_KEY is not already set in the
# environment. The API then listens on http://localhost:8000 and every
# diagnosis run streams to the Agent Flight Recorder dashboard.

$env:AFREC_API_URL = "https://afrec-api.onrender.com"
$env:REVGUARD_AI_PROVIDER = "groq"
$env:PYTHONPATH = "src"

if (-not $env:GROQ_API_KEY) {
    $env:GROQ_API_KEY = Read-Host "Paste your Groq API key (gsk_...)"
}

py -3.12 -m uvicorn revguard.api.app:create_api_app --factory --port 8000
