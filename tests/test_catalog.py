from kai.engine.catalog import bundles
from kai.service import story_failures


EXPECTED = {
    "low-balance-top-up": (23, 7),
    "low-balance-alert": (19, 6),
    "sweep-between-accounts": (18, 6),
    "fx-rate-watch": (18, 5),
    "jit-card-topup": (13, 4),
    "individual-onboarding": (81, 13),
    "business-onboarding": (106, 21),
}


def test_every_workflow_matches_its_stories():
    rows = bundles()
    assert [row["key"] for row in rows] == list(EXPECTED)
    untyped = 0
    for row in rows:
        steps, stories = EXPECTED[row["key"]]
        assert len(row["graph"]["steps"]) == steps, row["key"]
        assert len(row["stories"]) == stories, row["key"]
        failed = story_failures(row["graph"], row["stories"])
        assert failed == [], (row["key"], failed)
        for step in row["graph"]["steps"]:
            if row["key"].endswith("onboarding") and not step.get("type"):
                untyped += 1
    assert untyped == 185
