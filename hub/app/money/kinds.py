"""What kind of account each one is, from its name, unless a member said otherwise.

A bank names its accounts in its own way ("Everyday Checking", "Platinum Visa", "Roth IRA", "529 College Plan").
The kind decides where an account is drawn, whether its balance is owed or owned, and what the plan does with
it. A member can set it on the account's sheet or in Ask, and that is kept (`kind_set`).
"""
import re

# (kind, what it is called, the words that say so), most particular first: a "Health Savings Account" is not
# savings, a "Home Loan" is not the home, an "Auto Loan" is not the car.
KINDS = [
    ('hsa', 'Health savings', r'\bhsa\b|health savings'),
    ('college', 'College savings', r'\b529\b|college|education savings|\besa\b'),
    ('retirement', 'Retirement', r'401\s?\(?k\)?|403\s?\(?b\)?|\b457\b|\bira\b|\broth\b|\bsep\b|\bsimple ira\b|\btsp\b|pension|retire'),
    ('mortgage', 'Mortgage', r'mortgage|home loan|\bheloc\b|home equity'),
    ('loan', 'Loan', r'\bloan\b|line of credit|\bloc\b|student|financing'),
    ('card', 'Credit card', r'credit|\bcard\b|\bvisa\b|mastercard|\bamex\b|american express|discover|sapphire|freedom'),
    ('savings', 'Savings', r'saving|money market|\bmma\b|high yield|\bcd\b|certificate|reserve|emergency'),
    ('brokerage', 'Investments', r'brokerage|invest|individual|trading|\bstocks?\b|taxable|portfolio|\betf\b'),
    ('checking', 'Checking', r'checking|chequing|everyday|spending|share draft|debit|current'),
    ('property', 'Home', r'\bhome\b|\bhouse\b|property|real estate|condo'),
    ('vehicle', 'Car', r'\bcar\b|vehicle|truck|\bauto\b|motorcycle'),
    ('cash', 'Cash', r'\bcash\b|wallet'),
]
NAMES = {kind: name for kind, name, _ in KINDS} | {'other': 'Other'}
OWED = frozenset({'card', 'loan', 'mortgage'})              # a balance here is money owed
CASH = frozenset({'checking', 'savings', 'cash'})
SPENDING = frozenset({'checking', 'card', 'cash'})          # where everyday money is spent from
INVESTED = frozenset({'retirement', 'brokerage', 'hsa', 'college'})
OWNED = frozenset({'property', 'vehicle'})
# How the screen groups them, in order.
GROUPS = [('Cash', CASH), ('Cards', {'card'}), ('Investments', INVESTED), ('Loans', {'loan', 'mortgage'}),
          ('Things you own', OWNED), ('Other', {'other'})]


def guess(name, bank=''):
    """The kind an account's name says it is, else 'other'."""
    said = ' '.join(f'{name} {bank}'.lower().split())
    for kind, _, words in KINDS:
        if re.search(words, str(name).lower()):
            return kind
    # A name that says nothing ("Primary", "Joint") is most often the everyday account.
    return 'checking' if re.search(r'primary|joint|main|household', said) else 'other'


def worth(kind, balance):
    """What an account adds to the household's net worth: owed balances count against it, however the bank
    signs them (some report a card's balance as a positive amount owed, some as negative)."""
    if balance is None:
        return 0.0
    return -abs(balance) if kind in OWED else float(balance)


def group(kind):
    return next((name for name, kinds in GROUPS if kind in kinds), 'Other')
