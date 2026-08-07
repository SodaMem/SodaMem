import pytest

models = pytest.importorskip(
    "sodamem.models",
    reason="I4 gate activates when models are ported in Phase 1",
)

# `sodamem/models` already exists as an empty stub package (scaffolded for the
# I2/I3 import-linter layering contract), so importorskip alone can't detect
# "Phase 1 not landed" — it imports fine with nothing in it. Gate on the actual
# sentinel symbol instead so this stays fail-closed (skip) until Phase 1 defines
# it, and auto-activates (real assertions run) the moment it does.
if getattr(models, "AnswerEvidenceBundle", None) is None:
    pytest.skip(
        "I4 gate activates when models are ported in Phase 1 "
        "(AnswerEvidenceBundle not yet defined)",
        allow_module_level=True,
    )

# 允许的证据字段集合；任何“阅读/答题指令”字段（如 answer_task / reading_method /
# preferred_answer_style / official_* ）都不得出现在 memory 层交付的 bundle 上。
_FORBIDDEN_SUBSTRINGS = ("answer_task", "reading_method", "answer_style", "official_", "cot")


def test_answer_bundle_carries_no_reading_instructions():
    bundle_cls = getattr(models, "AnswerEvidenceBundle", None)
    assert bundle_cls is not None, "AnswerEvidenceBundle expected in sodamem.models"
    field_names = set(getattr(bundle_cls, "model_fields", {}) or getattr(bundle_cls, "__dataclass_fields__", {}))
    leaked = {f for f in field_names for s in _FORBIDDEN_SUBSTRINGS if s in f.lower()}
    assert not leaked, f"memory layer is leaking reading instructions into the bundle: {leaked}"
