# Product prototype

A clickable sketch of the proposed Hub: the house as a plan, Today, Kitchen, System and Ask.
Static files and made-up data only (`data.js`: fictional people, rooms, music, mail, school and shop).
It talks to no server, so it cannot reach a real house.

## Run it

```sh
python3 -m http.server 8911 --bind 127.0.0.1 --directory web/proto
```

Open `http://127.0.0.1:8911/`. Narrow the window for the phone layout; 1040 px and wider shows the
laptop layout with Ask always open. `board.html` frames the main states side by side
(`?theme=day`, `?set=wide`).

## What you can do

- Tap a room to switch its lights. Hold it (or use the dots on a laptop) for everything in the room.
- Tap a scene, the bar that shows what is playing, or Ask.
- In Ask, type a room's first letters and switch it in the list, or send one of the six scripted
  requests: dim downstairs, play this in the dining room too, what's on tomorrow, something new for
  dinner, tacos tonight, add them to the cart.
- In Kitchen, open one of the dinners under "New to you this week": tick off anything you're out of,
  then add only what is missing to the cart. Anything else gets a note that the prototype is scripted.

## States by address

The address picks the state, so each can be opened and photographed:

| Address | Shows |
| --- | --- |
| `#home`, `#today`, `#kitchen`, `#system` | The four screens |
| `?theme=dusk` or `?theme=day` | The two looks |
| `?sheet=room:kitchen`, `?sheet=playing`, `?sheet=ask`, `?sheet=idea:curry` (with `#kitchen`) | A room, the music sheet, Ask, a new recipe |
| `?ask=type`, `lights`, `music`, `tomorrow`, `fresh`, `tacos`, `shop` | Ask while typing, and six answers |
| `?state=offline`, `loading`, `quiet`, `empty` (with `#today`) | Can't reach home, loading, no music, nothing needs you |
| `?still=1` | No opening animation or thinking pause, for screenshots |

## The typeface

Jost (`fonts/Jost.ttf`), under the SIL Open Font License; the licence is beside it.
