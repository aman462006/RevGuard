# Run ONE real Groq diagnosis directly with afrec recording.
# No Razorpay keys needed - this is the AI step only, not the full pipeline.
# Run from the repo root:
#
#     powershell -ExecutionPolicy Bypass -File scripts\demo_diagnose_direct.ps1

$env:AFREC_API_URL = "https://afrec-api.onrender.com"
$env:PYTHONPATH = "src"

if (-not $env:GROQ_API_KEY) {
    $env:GROQ_API_KEY = Read-Host "Paste your Groq API key (gsk_...)"
}

py -3.12 scripts\demo_diagnose_direct.py
