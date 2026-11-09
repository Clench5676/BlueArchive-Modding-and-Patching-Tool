"""The version of the costume build rule (Switch 3D Model): what keys the saved verdicts and preview builds of costume
pairs, so a new program build reuses them unless the rule itself changed. Bump it with any change to what the costume
build writes, refuses or measures; ``tests/test_costume_rule.py`` pins the rule's sources to it."""

COSTUME_RULE = 2

# the modules whose code decides a costume build (relative to the ``bmpt`` package)
RULE_SOURCES = ("gameassets/model_adapt.py", "gameassets/model_hems.py", "gameassets/model_skirts.py",
                "gameassets/model_geometry.py")
