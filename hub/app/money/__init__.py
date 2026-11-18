"""Money: the household's bank, card, loan and investment accounts, read and never moved.

What it is. Each morning (and when someone opens Money and the last reading is old) Hub reads every account a
member connected: balances and the last few weeks of transactions, through SimpleFIN Bridge (read-only by its
design: it has no way to pay, transfer or trade) or from a statement file a member gives it. From that, in code
and with no model, Hub works out where the money goes (categories, transfers between the household's own
accounts), what comes back every month (bills, subscriptions, paydays), how the month is going against the
budget, what cash will look like over the next weeks, how the goals are coming along and when retirement works.

Ask is the way in (owner's rule, 2026-11-16: one entry point that has the context and can steer everything).
"How are we doing this month?", "can we afford a $4,000 trip in March?", "set groceries to $700" and "when can
we retire?" are answered there, with a card drawn under the answer. Money is also one screen in the app, the
picture of it all, opened from Ask, Today and System rather than a tab of its own.

Hub never moves money. No code here can pay, transfer, trade or change anything at a bank: the one network
client (simplefin.py) refuses every request but claiming a setup token and reading accounts. What a member
changes (a budget, a category, a goal, the plan's assumptions, a value typed in, who sees an account) is Hub's
own record, kept with the way back, and Undo puts it back.

Who sees what (owner decision, 2026-11-16, "Shared, with private accounts"): both members see every account and
the family's budget and plans; either can mark one of their own accounts private, and the other then sees only
its balance, in the totals, never its transactions. A private account stays out of the household's budget. No
login (a program on this Mac, the kitchen wall) sees nothing of Money.

What reaches a model (owner decision, 2026-11-16, "What a question needs, like mail"): Ask's model is given what
a money question needs (balances, budgets, bills, matching transactions), never account or card numbers, and
nothing at all for a member who switched "Money in Ask" off. Nothing of Money goes to Luna, the wall or the
morning push.

store.py keeps it (money.db, owner-only); simplefin.py and files.py read it; kinds.py and categories.py name it;
sync.py brings a reading in; recurring.py, budget.py, ahead.py and plan.py work it out; checks.py says what needs
a person; changes.py is every change with its Undo; share.py is who sees what; picture.py is what the app and
Today see; ask.py is what Ask sees and changes; routes.py is the app's door to all of it.
"""
