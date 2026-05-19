import importlib.util
import pathlib


def test_v2_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_verification_metadata_v2*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("v2_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "edb2e385f26b"


def test_prediction_value_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_prediction_value*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("prediction_value_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "30fd925789cb"


def test_prediction_context_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_prediction_context*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("prediction_context_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "8df4e2013c5a"
