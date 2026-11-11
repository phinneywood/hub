from .gmail import TOOL as GMAIL
from .network import TOOL as NETWORK
from .mobility import TOOL as MOBILITY
from .calendar import TOOL as CALENDAR

from preschool import TOOL as PRESCHOOL

from .trip import TOOL as TRIP
from .kitchen import TOOL as KITCHEN
from ..agent import TOOL as ASK

TOOLS = [ASK, GMAIL, CALENDAR, KITCHEN, PRESCHOOL, NETWORK, MOBILITY, TRIP]
