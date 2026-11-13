"""One answer for a tap, in the shape the caller asked for.

The Notes, Recipes and Playlists pages post forms and want a redirect back. The chat's cards post the same
routes with `Accept: application/json` and want {"ok": true, "said": …} or {"error": …}. Both go through the
same route and the same checks; only the answer's shape differs.
"""
from fastapi.responses import JSONResponse, RedirectResponse


def wants_json(request):
    return 'application/json' in request.headers.get('accept', '')


def done(request, back, said=''):
    if wants_json(request):
        return JSONResponse({'ok': True, 'said': said})
    return RedirectResponse(back, status_code=303)


def refused(request, problem, page=None, status=409):
    """`page()` renders the HTML answer for a form; a card gets the problem as JSON."""
    if wants_json(request):
        return JSONResponse({'error': str(problem)[:300]}, status_code=status)
    return page()
