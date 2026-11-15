"""Ask, inside the app: say what you want and Hub does it or answers.

- `route.py`: the one model Ask uses, and the one way it is reached.
- `private.py`: what Ask may read about mail, the calendar, school and today's list, and Luna's answer to those
  on a Hub without Ask's model.
- `tools.py`: what Ask can do: lights, scenes, music, remembering, passing a message to Kitchen, looking up the day.
- `brain.py`: one turn, from the owner's words to what was done and said.
- `turns.py`: what was asked and answered, kept for a month; what is often asked around now; what Hub remembers.

Read "Ask" in docs/app.md before changing what reaches which model.
"""
