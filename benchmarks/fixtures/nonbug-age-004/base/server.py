"""Synthetic case service. The hidden oracle is outside this tree."""
import csv
import os
from datetime import date, datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

def valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False

def valid_offset(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False

def classify(value: str) -> tuple[int, str]:
    if not value.isdecimal() or int(value) < 18:
        return 422, 'Rejected'
    return 200, 'Accepted'

@app.get('/__aip_health')
def health():
    return JSONResponse({'ok': True}, headers={
        'x-aip-fixture-instance': os.environ.get('AIP_FIXTURE_INSTANCE_ID', ''),
    })

@app.get('/', response_class=HTMLResponse)
def index():
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Benchmark case</title><body><h1>Benchmark case</h1>
<label for="value">Value</label><textarea id="value"></textarea>
<button id="check">Check</button><output id="result" aria-live="polite"></output>
<script>document.getElementById('check').addEventListener('click', async () => {
const response = await fetch('/api/check', {method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({value:document.getElementById('value').value})});
const data = await response.json();
document.getElementById('result').textContent = response.status + ':' + data.message;
});</script></body></html>'''

@app.post('/api/check')
def check(payload: dict):
    value = payload.get('value', '')
    if not isinstance(value, str) or len(value) > 10000:
        return JSONResponse({'message': 'Rejected'}, status_code=422)
    status, message = classify(value)
    return JSONResponse({'message': message}, status_code=status)
