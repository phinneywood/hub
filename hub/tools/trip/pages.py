import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from ...templating import render
from . import store, domain
router = APIRouter()

@router.get('')
def home(request: Request):
    return render(request, 'trip/index.html', active='trip')

@router.get('/data')
def data():
    return store.load()

@router.get('/export')
def export():
    return JSONResponse(store.load(), headers={'Content-Disposition': 'attachment; filename="nyc-nj-trip.json"'})

@router.post('/save')
async def save(request: Request):
    body = await request.body()
    if len(body) > 500_000:
        raise HTTPException(413, 'Trip file is too large.')
    try:
        payload = json.loads(body)
        revision = payload['expected_revision']
        doc = payload['trip']
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError('Invalid revision.')
        current = store.load()
        # Imports and edits cannot forge the audit trail or replace another trip.
        doc['history'] = current.get('history', [])
        doc['revision'] = current['revision']
        result = store.save(domain.validate(doc), revision)
        return result
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({'error': str(exc)}, status_code=409 if 'revision' in str(exc).lower() or 'conflict' in str(exc).lower() else 400)
