"""Where the money goes: a category for every transaction, and the payee's name as people say it.

Banks send a line of text ("POS DEBIT TRADER JOE S #552 11/14"), not a category. Hub cleans the line, names the
payee ("Trader Joe's") and picks a category, in this order:

1. A member's own rule ("Costco is groceries"), the longest that fits. A correction always wins.
2. A transfer between the household's own accounts (sync.py pairs them first); a card's own payment line.
3. Hub's built-in words: well-known shops and services, and plain words ("pharmacy", "insurance", "payroll").
4. Otherwise "Other" for money out, "Income" for money in.

No model is asked. The built-in list holds only nationally known names and plain words: the household's own
payees are learned from its corrections, which stay in Hub's private folder.
"""
import re

# (key, what it is called, its group). Groups: everyday spending, bills, money in, and money that only moves
# between the household's own accounts, which is never spending.
CATEGORIES = [
    ('groceries', 'Groceries', 'everyday'), ('dining', 'Eating out', 'everyday'), ('household', 'Household', 'everyday'),
    ('shopping', 'Shopping', 'everyday'), ('transport', 'Getting around', 'everyday'), ('kids', 'Kids', 'everyday'),
    ('health', 'Health', 'everyday'), ('personal', 'Personal care', 'everyday'), ('fun', 'Fun', 'everyday'),
    ('gifts', 'Gifts and giving', 'everyday'), ('travel', 'Travel', 'everyday'), ('pets', 'Pets', 'everyday'),
    ('other', 'Other', 'everyday'),
    ('housing', 'Housing', 'bills'), ('utilities', 'Utilities', 'bills'), ('phone', 'Phone and internet', 'bills'),
    ('insurance', 'Insurance', 'bills'), ('subscriptions', 'Subscriptions', 'bills'), ('childcare', 'Childcare and school', 'bills'),
    ('loans', 'Loan payments', 'bills'), ('cards', 'Card payments', 'bills'), ('taxes', 'Taxes', 'bills'), ('fees', 'Fees', 'bills'),
    ('income', 'Income', 'in'), ('interest', 'Interest and dividends', 'in'), ('refunds', 'Refunds', 'in'),
    ('transfer', 'Between your accounts', 'moves'), ('saving', 'Saving and investing', 'moves'),
]
NAMES = {key: name for key, name, _ in CATEGORIES}
GROUP = {key: group for key, _, group in CATEGORIES}
SPENT = frozenset(k for k, g in GROUP.items() if g in ('everyday', 'bills'))     # what a budget counts
EVERYDAY = frozenset(k for k, g in GROUP.items() if g == 'everyday')
BILLS = frozenset(k for k, g in GROUP.items() if g == 'bills')
VARIES = frozenset({'utilities'})           # bills that are a different amount each time, however steady the last few were
MOVES = frozenset(k for k, g in GROUP.items() if g == 'moves')
# Spending a family can't skip for a month: what an emergency fund has to cover.
ESSENTIAL = frozenset({'groceries', 'household', 'transport', 'health', 'kids', 'housing', 'utilities', 'phone', 'insurance',
                       'childcare', 'loans', 'cards', 'taxes'})

# ---- the payee's line, cleaned ----

NOISE = re.compile(
    r'\b(?:pos|debit card purchase|debit card|debit|purchase authorized on|purchase|checkcard|check card|recurring payment|'
    r'recurring|preauthorized|pre-authorized|ach|ppd|ccd|web|tel|indn|co id|id|aplpay|apple pay|sq|tst|sp|py|dd|bt|'
    r'paypal|pp|visa|mc|card \d+|xx+\d*|x{2,}\d+)\b', re.I)
MASK = re.compile(r'(?:\*{2,}|x{2,}|\.{2,}|#)\s?\d{2,}|\b\d{4,}\b|\b[a-z]{1,2}\d{3,}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b', re.I)
STOP = frozenset('the inc llc co com corp ltd store stores market www us usa online the of and #'.split())
STATES = frozenset(('al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd '
                    'oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc').split())

# Names people say, for lines that spell them otherwise.
ALIASES = [
    (r'amzn|amazon(?! prime)', 'Amazon'), (r'amazon prime|prime video', 'Amazon Prime'), (r'netflix', 'Netflix'),
    (r'spotify', 'Spotify'), (r'apple\.?com|itunes|apple services', 'Apple'), (r'google', 'Google'), (r'uber\s?eats', 'Uber Eats'),
    (r'\buber\b', 'Uber'), (r'\blyft\b', 'Lyft'), (r'doordash', 'DoorDash'), (r'grubhub', 'Grubhub'), (r'instacart', 'Instacart'),
    (r'starbucks', 'Starbucks'), (r'costco', 'Costco'), (r'trader joe', 'Trader Joe’s'), (r'whole ?foods|wholefds', 'Whole Foods'),
    (r'safeway', 'Safeway'), (r'fred ?meyer', 'Fred Meyer'), (r'kroger', 'Kroger'), (r'\bqfc\b', 'QFC'), (r'target', 'Target'),
    (r'wal-?mart', 'Walmart'), (r'home ?depot', 'Home Depot'), (r'lowe.?s', 'Lowe’s'), (r'\bikea\b', 'IKEA'), (r'\brei\b', 'REI'),
    (r'chevron', 'Chevron'), (r'\bshell\b', 'Shell'), (r'\barco\b', 'ARCO'), (r'exxon|\bmobil\b', 'ExxonMobil'), (r'walgreens', 'Walgreens'),
    (r'\bcvs\b', 'CVS'), (r'disney ?\+|disneyplus', 'Disney+'), (r'\bhulu\b', 'Hulu'), (r'\bhbo\b|\bmax\.com', 'Max'),
    (r'youtube', 'YouTube'), (r'audible', 'Audible'), (r'comcast|xfinity', 'Xfinity'), (r'verizon', 'Verizon'),
    (r't-?mobile', 'T-Mobile'), (r'\bat&?t\b', 'AT&T'), (r'venmo', 'Venmo'), (r'zelle', 'Zelle'), (r'chewy', 'Chewy'),
    (r'petco', 'Petco'), (r'petsmart', 'PetSmart'), (r'airbnb', 'Airbnb'), (r'dropbox', 'Dropbox'), (r'adobe', 'Adobe'),
    (r'1password', '1Password'), (r'openai|chatgpt', 'OpenAI'), (r'anthropic|claude\.ai', 'Anthropic'), (r'github', 'GitHub'),
    (r'peloton', 'Peloton'), (r'patreon', 'Patreon'), (r'\birs\b|us treasury|treas tax', 'IRS'),
]

# Hub's own guesses: (category, words). Only well-known names and plain words; checked in order.
BUILT_IN = [
    ('fees', r'overdraft|\bnsf\b|insufficient funds|late fee|annual fee|foreign transaction|atm fee|service fee|monthly fee|'
             r'maintenance fee|interest charge|finance charge|returned item'),
    ('subscriptions', r'netflix|spotify|hulu|disney ?\+|disneyplus|\bhbo\b|max\.com|youtube premium|youtube tv|audible|apple\.?com|'
                      r'itunes|icloud|amazon prime|prime video|dropbox|adobe|1password|openai|chatgpt|anthropic|claude\.ai|github|'
                      r'peloton|patreon|new york times|nytimes|wsj|substack|paramount|peacock|sirius|duolingo|headspace|calm\.com|'
                      r'microsoft 365|google storage|google one|nintendo online|playstation plus|xbox game pass'),
    ('groceries', r'grocer|supermarket|trader joe|whole ?foods|wholefds|safeway|fred ?meyer|kroger|\bqfc\b|albertsons|\baldi\b|'
                  r'publix|wegmans|h-?e-?b\b|sprouts|instacart|costco|food co-?op|market basket|winco|stop & shop|giant eagle|'
                  r'harris teeter|food lion|ralphs|vons|meijer|hy-?vee|natural grocers|new seasons|pcc'),
    ('dining', r'restaurant|\bcafe\b|caf[eé]|coffee|espresso|starbucks|dunkin|mcdonald|burger|chipotle|taco|taqueria|pizza|'
               r'sushi|ramen|pho\b|thai|bistro|kitchen|grill|diner|bakery|doordash|uber\s?eats|grubhub|postmates|seamless|'
               r'panera|subway|wendy|chick-?fil|five guys|shake shack|domino|papa john|brewing|brewery|taproom|\bbar\b|pub\b'),
    ('transport', r'chevron|\bshell\b|\barco\b|exxon|\bmobil\b|\b76\b|texaco|valero|sunoco|\bbp\b|gas station|fuel|\buber\b|\blyft\b|'
                  r'parking|park ?mobile|paybyphone|transit|metro|\bbus\b|toll|good to go|fastrak|ez-?pass|dmv|car wash|'
                  r'jiffy lube|auto repair|tire|supercharger|ev charging|chargepoint|amtrak|\bferry\b'),
    ('utilities', r'electric|energy|power|\bwater\b|sewer|garbage|waste management|recology|utilit|\butil\b|city light|natural gas|gas & electric|'
                  r'light & power|\bpge\b|pg&e|con ed|duke energy|dominion'),
    ('phone', r'comcast|xfinity|verizon|t-?mobile|\bat&?t\b|sprint|spectrum|cox comm|centurylink|lumen|frontier comm|'
              r'google fi|mint mobile|visible|ziply|wave broadband|astound|starlink|internet|wireless|cable'),
    ('insurance', r'insurance|insur\b|geico|state farm|allstate|progressive|lemonade|liberty mutual|farmers ins|nationwide|'
                  r'usaa ins|metlife|prudential|life ins|term life|haven life|policygenius'),
    ('housing', r'mortgage|\brent\b|rental|apartments?|property management|\bhoa\b|homeowners assoc|home loan|escrow'),
    ('loans', r'loan payment|student loan|navient|nelnet|mohela|great lakes|sallie mae|auto loan|car loan|sofi loan|lending club'),
    ('childcare', r'daycare|day care|childcare|child care|preschool|montessori|tuition|school|bright horizons|kindercare|'
                  r'nanny|babysit|after ?school|summer camp|\bcamp\b'),
    ('kids', r'toys|toy store|kids|children|baby|babies|carter.?s|gap kids|lego|diaper'),
    ('health', r'pharmacy|walgreens|\bcvs\b|rite aid|dental|dentist|orthodont|clinic|hospital|medical|doctor|physician|'
               r'urgent care|optometr|vision|eye care|lab corp|labcorp|quest diag|therapy|counsel|kaiser|health'),
    ('personal', r'salon|barber|hair|spa\b|nails|sephora|ulta|cosmetic|gym|fitness|yoga|climbing'),
    ('pets', r'chewy|petco|petsmart|\bvet\b|veterinar|animal hosp|pet supplies|groom'),
    ('travel', r'airline|airlines|alaska air|delta air|united air|southwest|american air|jetblue|airbnb|vrbo|hotel|marriott|'
               r'hilton|hyatt|expedia|booking\.com|hotels\.com|kayak|car rental|hertz|avis|enterprise rent|national car'),
    ('fun', r'cinema|theater|theatre|\bamc\b|regal|museum|\bzoo\b|aquarium|tickets?|ticketmaster|eventbrite|stubhub|steam|'
            r'playstation|nintendo|xbox|bowling|concert|bookstore|books\b|barnes'),
    ('gifts', r'donation|donate|charity|gofundme|red cross|unicef|salvation army|goodwill|church|temple|giving|gift'),
    ('household', r'home ?depot|lowe.?s|ace hardware|hardware|true value|ikea|wayfair|bed bath|container store|cleaning|'
                  r'laundry|dry clean|furniture|garden|nursery|plumb|electrician|handyman|pest'),
    ('taxes', r'\birs\b|us treasury|treas tax|tax payment|franchise tax|dept of revenue|department of revenue|property tax|'
              r'county treasurer'),
    ('shopping', r'amazon|amzn|target|wal-?mart|best buy|etsy|ebay|nordstrom|macy|\brei\b|old navy|\bgap\b|uniqlo|zara|'
                 r'h&m|tj ?maxx|marshalls|ross stores|kohl|apple store|costco\.com|shop'),
]
IN_WORDS = [
    ('income', r'payroll|direct dep|dir dep|salary|paycheck|\bpay\b|wages|adp|gusto|paychex|employer|irs treas|tax refund|'
               r'state tax refund|unemployment|social security|ssa treas|pension'),
    ('interest', r'interest|dividend|\bdiv\b|\bint\b paid|yield'),
    ('refunds', r'refund|return|reversal|credit adj|adjustment|cashback|cash back|reward'),
]
# A card's own line for the payment that cleared it, and a payment out of checking to a card.
CARD_PAID = re.compile(r'payment|thank you|autopay|auto pay|auto-pay|online pmt|pymt|pmt received|e-?pay', re.I)
TO_A_CARD = re.compile(r'card ?(?:services|payment|pymt|pmt|member)|cardmember|credit ?card|crd ?(?:pmt|epay|autopay)|credit ?crd|apple ?card|'
                       r'amex e-?payment|discover e-?payment|capital one (?:mobile|online) pmt|citi (?:card|autopay)', re.I)
MOVED = re.compile(r'\btransfer\b|\bxfer\b|\btrnsfr\b|to savings|from savings|to checking|from checking|online banking transfer|'
                   r'internal transfer', re.I)
PEOPLE = re.compile(r'venmo|zelle|cash app|square cash|paypal', re.I)      # money to or from a person, not a shop


def clean(line):
    """A bank's line without the bank's own words, card masks, dates and long numbers, lowercased."""
    text = str(line or '').replace('*', ' ').replace('_', ' ')
    text = MASK.sub(' ', text)
    text = NOISE.sub(' ', text)
    text = re.sub(r'[^\w&.+\'’-]+', ' ', text)
    return ' '.join(text.lower().split())


def name(line):
    """The payee as people say it: a known name, else the line's first words in title case."""
    said = clean(line)
    for words, called in ALIASES:
        if re.search(words, said):
            return called
    words = [w for w in said.split() if w not in STOP and not w.isdigit()]
    if len(words) > 2 and words[-1] in STATES:                 # "... springfield wa": the state, then the town
        words = words[:-2]
    called = ' '.join(words[:3]).strip(" .-'’") or 'Unknown'
    called = ' '.join(w if any(c.isdigit() for c in w) else w[:1].upper() + w[1:] for w in called.split())
    return re.sub(r"\b(\w+) S\b", r"\1’s", called)


def key(line):
    """What groups one payee's lines together, however the bank wrote each one."""
    said = name(line).lower()
    return ' '.join(said.split()[:2])


def builtin(line, amount):
    """Hub's own guess from the line and which way the money went: a category, or None."""
    said = clean(line)
    if amount < 0:
        return next((category for category, words in BUILT_IN if re.search(words, said)), None)
    for category, words in IN_WORDS:              # pay, interest: not money back from a shop
        if category != 'refunds' and re.search(words, said):
            return category
    for category, words in BUILT_IN:              # money back from a shop: a refund, netted against what it was spent on
        if category in EVERYDAY and re.search(words, said):
            return category
    return next((category for category, words in IN_WORDS if category == 'refunds' and re.search(words, said)), None)


def by_rule(line, rules):
    """The member's rule that fits this line best (the longest words), or None. `rules`: [(words, category)]."""
    said = ' ' + clean(line) + ' '
    named = ' ' + name(line).lower() + ' '
    fits = [(len(words), category) for words, category in rules
            if (' ' + words + ' ') in said or (' ' + words + ' ') in named]
    return max(fits)[1] if fits else None


def rule_words(words):
    """What a member said a rule is about ("Costco", "the Costco gas station"), as the words it matches."""
    plain = clean(re.sub(r"^(?:the|my|our|all|every)\s+", '', str(words or '').strip(), flags=re.I))
    plain = re.sub(r'\b(?:charges?|payments?|purchases?|transactions?)\b$', '', plain).strip()
    return plain[:60]


def guess(line, amount, kind, rules=()):
    """(category, how) for one transaction, before transfers are paired. `kind`: the account's kind."""
    ruled = by_rule(line, rules)
    if ruled:
        return ruled, 'rule'
    said = clean(line)
    if kind in ('card', 'loan', 'mortgage') and amount > 0 and CARD_PAID.search(said):
        return 'transfer', 'payment'
    if amount < 0 and TO_A_CARD.search(said) and not re.search(r'store|shop', said):
        return 'cards', 'payment'
    if PEOPLE.search(said):
        return ('other' if amount < 0 else 'income'), 'person'
    if MOVED.search(said):
        return 'transfer', 'words'
    found = builtin(line, amount)
    if found:
        return found, ('refund' if amount > 0 and found in EVERYDAY else 'built-in')
    return ('income' if amount > 0 else 'other'), 'default'


def named(words):
    """The category a member meant ("groceries", "eating out", "restaurants", "gas"), or None."""
    plain = ' '.join(str(words or '').lower().replace('&', 'and').split()).removeprefix('the ').strip()
    if not plain:
        return None
    for key_, called, _ in CATEGORIES:
        if plain in (key_, called.lower(), called.lower().rstrip('s'), key_.rstrip('s')):
            return key_
    for key_, words_ in SAID.items():
        if re.fullmatch(words_, plain):
            return key_
    return next((k for k, called, _ in CATEGORIES if plain in called.lower() or called.lower() in plain), None)


# What people call a category, when it isn't its name.
SAID = {
    'dining': r'(?:restaurants?|dining|dining out|eating out|takeout|take-out|food delivery|coffee|lunch(?:es)?|dinners? out)',
    'groceries': r'(?:grocery|food|supermarket|food shopping)',
    'transport': r'(?:gas|fuel|car|cars|transport|transportation|commute|parking|rides?)',
    'utilities': r'(?:electric(?:ity)?|power|water|heat|heating|utility)',
    'phone': r'(?:internet|cell|cellphones?|phones?|mobile|wifi)',
    'housing': r'(?:rent|mortgage|home|house|hoa)',
    'subscriptions': r'(?:streaming|apps?|memberships?|subs)',
    'childcare': r'(?:daycare|school|preschool|tuition|nanny|childcare)',
    'kids': r'(?:children|baby|toys|kid stuff)',
    'health': r'(?:medical|doctor|doctors|pharmacy|dental|healthcare)',
    'personal': r'(?:haircuts?|beauty|gym|fitness|self care)',
    'fun': r'(?:entertainment|fun money|hobbies|movies|games)',
    'gifts': r'(?:gifts?|donations?|charity|giving)',
    'travel': r'(?:vacations?|trips?|holidays?|flights?|hotels?)',
    'household': r'(?:home improvement|house stuff|hardware|furniture|cleaning)',
    'shopping': r'(?:clothes|clothing|amazon|stuff)',
    'saving': r'(?:savings?|investing|investments?|retirement contributions?)',
    'transfer': r'(?:transfers?|moving money|between accounts)',
    'fees': r'(?:bank fees|charges)',
    'income': r'(?:pay|salary|paychecks?|wages)',
}
