import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_importlinter_contract_file_exists():
    assert (REPO / ".importlinter").is_file()


def test_import_contracts_hold():
    # `--extra dev` is not optional: `import-linter` lives in the dev extra,
    # and a bare `uv run` syncs only the default dependency set. Without it
    # this test failed with "Failed to spawn: lint-imports" on every clean
    # machine — a red test that says nothing about the contracts, which is
    # worse than no test because it trains people to ignore the failure.
    r = subprocess.run(["uv", "run", "--extra", "dev", "lint-imports"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, f"import-linter failed:\n{r.stdout}\n{r.stderr}"
