"""Hub speaks in sentences. These say a room the way people do, for the sentences Hub writes itself (Ask's
answers). The app has the same words for the sentences it writes (web/app/src/words.ts); a test keeps them equal.
"""
# A room made only of these everyday words is said that way ("in the kitchen", "in the front yard"); one with a
# name in it ("Robin’s room") keeps its name.
EVERYDAY = frozenset((
    'kitchen living dining room bedroom office hallway hall bath bathroom porch yard basement garage '
    'entry study nursery den deck pantry laundry foyer stairs guest family sun attic patio garden front back side '
    'upstairs downstairs primary main sitting area door play playroom mud mudroom closet loft landing lower upper '
    'tv media library workshop shed driveway balcony terrace hallway lounge kids').split())
# A few rooms are named in shorthand on the plan and said in full.
FULL = {'dining': 'dining room', 'bath': 'bathroom', 'guest': 'guest room', 'living': 'living room'}


def spoken(name):
    plain = ' '.join(str(name).lower().split())
    return 'the ' + FULL.get(plain, plain) if plain and all(word in EVERYDAY for word in plain.split()) else str(name)


def listed(items):
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def sentence(text):
    """The words with a capital to start."""
    return text[:1].upper() + text[1:]
