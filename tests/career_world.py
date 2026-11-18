"""A made-up career desk for the tests and the demo: Alex, who builds tools that help engineers work with AI agents,
plans a next role and a small workshop business. Every name, product and employer here is invented."""
from hub import config

BRAND = """---
thesis: Alex designs how engineering teams hand work to AI agents, and where the agents stop and wait for a person.
headline: Engineering lead for agent tooling
---

# Brand

## Thesis

Alex designs how engineering teams hand work to AI agents, and where the agents stop and wait for a person.
"""

PLAN = """# Plan

## Next role
A staff role that designs how an engineering org works with agents.

- [ ] Rewrite the LinkedIn headline around the thesis
- [ ] Publish one essay a month for six months
- [x] Name the three kinds of role worth a look

## Side business
A two-day workshop for small teams.

- [ ] Price a two-day agent workshop
- [ ] Ask two teams what they would pay
"""

NOTES = """# Notes

## 2026-11-02 09:15

Shipped the garden sensors dashboard; the team stopped asking for weekly exports.

## 2026-11-12 18:40

The review agent only works when it stops before merging. The stop is the product.
"""

FIELD = """---
title: Agent platform roles in the city
date: 2026-11-10
summary: Agent platform and developer productivity teams are hiring program leads who can run evaluations.
by: Hub’s research desk
---

## What is happening

Three companies posted program roles for agent platforms this month. Each asks for evaluation and rollout experience.

## What it means for the plan

The essays should show evaluation work, not only tooling.

## Sources

- [Example jobs board](https://jobs.example.com/agent-platform)
- [A made-up survey](https://survey.example.org/2026)
"""

DRAFT = """---
title: The tap is the contract
date: 2026-11-12
status: draft
form: essay
by: Hub’s desk, a first draft
---

> Assistant draft from your desk. Rewrite it in your own words before it goes anywhere.

An agent that changes things should stop where a person has to own the change. At Examplecorp the review agent
did this before every merge.

## Sources

- [A made-up paper](https://papers.example.org/agents)
"""

IDEA = """---
title: One entry point
date: 2026-11-01
status: idea
---

Everything a household needs, steered from one place that can say no.
"""

WORK = """---
title: Garden sensors dashboard
summary: A dashboard that ended weekly exports for a team of six.
---

What it was, who used it, and what changed after.
"""

MARKET = """---
title: What the market asks for
summary: Program leads for agent platforms, with evaluation experience.
---

- Agent platform program lead: evaluations, rollouts, guardrails.
- Developer productivity lead: adoption, measurement.
"""

IP_NOTES = """# IP

Nothing about Project Lantern is published.
"""


def make(account='default', folder=None):
    """Write Alex's desk. Returns its folder."""
    root = folder or config.DATA_DIR / 'career' / account
    for name, text in (('brand.md', BRAND), ('plan.md', PLAN), ('narrative.md', NOTES), ('market.md', MARKET), ('ip.md', IP_NOTES),
                       ('ip-words.txt', 'Examplecorp\nProject Lantern  # the internal tool\n'),
                       ('field/2026-11-10-agent-platform-roles.md', FIELD), ('writing/2026-11-12-the-tap-is-the-contract.md', DRAFT),
                       ('writing/2026-11-01-one-entry-point.md', IDEA), ('work/garden-sensors.md', WORK)):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root
