"""Key saved costume verdicts and preview builds by the Switch 3D Model rule revision.

Program builds reuse them when the rule is unchanged. Bump the revision for changes to
what the build writes, refuses, or measures; tests/test_costume_rule.py pins its sources.
"""

COSTUME_RULE = 30

# the modules whose code decides a costume build (relative to the ``bmpt`` package)
RULE_SOURCES = (
    "gameassets/model_adapt/__init__.py",
    "gameassets/model_adapt/skeleton.py",
    "gameassets/model_adapt/parts.py",
    "gameassets/model_adapt/costume.py",
    "gameassets/model_adapt/skirts.py",
    "gameassets/model_adapt/face.py",
    "gameassets/model_adapt/halo.py",
    "gameassets/model_adapt/writing.py",
    "gameassets/model_adapt/transplant.py",
    "gameassets/model_hems.py",
    "gameassets/model_skirts.py",
    "gameassets/model_geometry.py",
    "gameassets/model_props.py",
    "gameassets/model_arms.py",
    "gameassets/model_arm_frames.py",
    "gameassets/model_arm_solve.py",
    "gameassets/model_arm_clips.py",
    "gameassets/costume_cache.py",
)
