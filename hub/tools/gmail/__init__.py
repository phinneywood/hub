from . import assess, explore, inbox, missions, organize, sweep

router = inbox.router  # the page at /gmail itself lives here, so this is the one the others join
router.include_router(missions.router)
router.include_router(explore.router)
router.include_router(organize.router)
router.include_router(sweep.router)
router.include_router(assess.router)

TOOL = {"slug": "gmail", "name": "Gmail", "description": "What needs you, what Hub sorted, and inbox cleanup", "router": router}
