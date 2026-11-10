from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["num"] = lambda n: f"{int(n):,}" if isinstance(n, (int, float)) else n
templates.env.filters["when"] = lambda ts: datetime.fromtimestamp(float(ts)).strftime("%b %-d, %-I:%M %p") if ts else ""


def render(request, name, active=None, status_code=200, **context):
    from .tools import TOOLS
    return templates.TemplateResponse(request, name, {"tools": TOOLS, "active": active, **context}, status_code=status_code)
