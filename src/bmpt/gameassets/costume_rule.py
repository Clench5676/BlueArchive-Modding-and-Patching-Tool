"""Key saved costume verdicts and preview builds by the Switch Chibi rule revision.

Program builds reuse them when the rule is unchanged. Bump the revision for changes to
what the build writes, refuses, or measures; tests/test_costume_rule.py pins its sources.
"""

COSTUME_RULE = 30

# the modules whose code decides a costume build (relative to the ``bmpt`` package)
RULE_SOURCES = (
    "gameassets/chibi_adapt/__init__.py",
    "gameassets/chibi_adapt/skeleton.py",
    "gameassets/chibi_adapt/parts.py",
    "gameassets/chibi_adapt/costume.py",
    "gameassets/chibi_adapt/skirts.py",
    "gameassets/chibi_adapt/face.py",
    "gameassets/chibi_adapt/halo.py",
    "gameassets/chibi_adapt/writing.py",
    "gameassets/chibi_adapt/transplant.py",
    "gameassets/chibi_hems.py",
    "gameassets/chibi_skirts.py",
    "gameassets/chibi_geometry.py",
    "gameassets/chibi_props.py",
    "gameassets/chibi_arms.py",
    "gameassets/chibi_arm_frames.py",
    "gameassets/chibi_arm_solve.py",
    "gameassets/chibi_arm_clips.py",
    "gameassets/costume_cache.py",
)
