import pytest

storage = pytest.importorskip(
    "sodamem.memory.storage",
    reason="I6 gate activates when storage is ported in Phase 1",
)
from sodamem.versioning import STORE_SCHEMA_VERSION  # noqa: E402

# `sodamem/memory/storage` already exists as an empty stub package (scaffolded
# for the I2/I3 import-linter layering contract), so importorskip alone can't
# detect "Phase 1 not landed" — it imports fine with nothing in it. Gate on the
# actual sentinel symbol instead so this stays fail-closed (skip) until Phase 1
# defines it, and auto-activates (real assertions run) the moment it does.
if getattr(storage, "open_store", None) is None:
    pytest.skip(
        "I6 gate activates when storage is ported in Phase 1 "
        "(storage.open_store not yet defined)",
        allow_module_level=True,
    )


def test_store_exposes_version_metadata():
    open_store = getattr(storage, "open_store", None)
    assert open_store is not None, "storage.open_store expected in Phase 1"
    # Phase 1 落地时补真实 store 路径；此处固定契约：store_meta 必含 schema_version + prompt_fingerprint
    meta = storage.store_meta_schema()  # 返回声明的元数据键集合
    assert "schema_version" in meta and "prompt_fingerprint" in meta
    assert meta["schema_version"] == STORE_SCHEMA_VERSION


def test_open_store_rejects_schema_mismatch(tmp_path):
    from sodamem.errors import StoreVersionError
    from sodamem.memory.storage.store import open_store

    class _FakeEmbedder:
        def embed(self, texts):
            return [[0.0] for _ in texts]

    db_path = tmp_path / "store.sqlite3"
    store = open_store(db_path, prompts={"extract": "v1"}, embedder=_FakeEmbedder())
    assert store.write_version("u1") == 0
    # Reopening with a DIFFERENT prompt must raise, not silently ALTER.
    with pytest.raises(StoreVersionError):
        open_store(db_path, prompts={"extract": "v2-drifted"}, embedder=_FakeEmbedder())
