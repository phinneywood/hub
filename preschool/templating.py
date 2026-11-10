from datetime import datetime
from pathlib import Path
from fastapi.templating import Jinja2Templates
import hub

templates = Jinja2Templates(directory=[str(Path(__file__).parent / "templates"),
                                         str(Path(hub.__file__).parent / "templates")])
from .presentation import date_label, time_label, prose_blocks
from . import school
# `{{ school.name }}`, `{{ school.short_name }}`: read from the private settings at render time.
templates.env.globals["school"] = school.live
templates.env.filters.update(family_date=date_label, family_time=time_label, family_prose=prose_blocks)
templates.env.filters["num"] = lambda n: f"{int(n):,}" if isinstance(n, (int, float)) else n
templates.env.filters["when"] = lambda ts: datetime.fromtimestamp(float(ts)).strftime("%b %-d, %-I:%M %p") if ts else ""

def render(request, name, active=None, status_code=200, **context):
    from hub.tools import TOOLS
    return templates.TemplateResponse(request, name, {"tools": TOOLS, "active": active, **context}, status_code=status_code)
