"""module-required cites the user's own manifest (the file and line where the module is
added), and is act-now through the narrow target-failure rule (zmig.tiers)."""

from __future__ import annotations

from pathlib import Path

from zmig.modulecheck import user_manifest
from zmig.tiers import tier

# The shape of CANnectivity v1.1.0's west.yml (repository root, one level above the app).
WEST = """manifest:
  remotes:
    - name: zephyrproject-rtos
      url-base: https://github.com/zephyrproject-rtos
  projects:
    - name: zephyr
      remote: zephyrproject-rtos
      revision: v4.1.0
      import:
        name-allowlist:
          - cmsis
          - hal_stm32
"""

TARGET_FAILURE = [
    {"type": "tool_output", "invocation": "target build", "excerpt": "`cmsis_core.h` is missing"},
    {
        "type": "tool_output",
        "invocation": "target manifest",
        "excerpt": "zephyr/west.yml:130 declares cmsis_6",
    },
]


def test_finds_the_manifest_above_the_app_and_its_allowlist(tmp_path: Path) -> None:
    (tmp_path / "cannectivity" / "app").mkdir(parents=True)
    (tmp_path / "cannectivity" / "west.yml").write_text(WEST)
    got = user_manifest(tmp_path / "cannectivity" / "app")
    assert got == (
        (tmp_path / "cannectivity" / "west.yml").resolve(),
        10,
        "the name-allowlist of the Zephyr import",
    )


def test_a_west_workspace_names_its_manifest(tmp_path: Path) -> None:
    (tmp_path / ".west").mkdir()
    (tmp_path / ".west" / "config").write_text("[manifest]\npath = fw\nfile = sub.yml\n")
    (tmp_path / "fw" / "app").mkdir(parents=True)
    (tmp_path / "fw" / "sub.yml").write_text("manifest:\n  projects:\n    - name: zephyr\n")
    (tmp_path / "fw" / "west.yml").write_text(WEST)  # not the workspace's manifest
    assert user_manifest(tmp_path / "fw" / "app") == (
        (tmp_path / "fw" / "sub.yml").resolve(),
        3,
        "the zephyr project entry",
    )


def test_no_manifest_within_reach(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "d" / "app"
    deep.mkdir(parents=True)
    (tmp_path / "west.yml").write_text(WEST)  # 5 levels up, outside git: too far
    assert user_manifest(deep) is None


def test_the_narrow_rule_is_module_required_only() -> None:
    # Without a project location (no manifest found): act-now through the target failure.
    assert tier("breaking", "high", TARGET_FAILURE, None, "module-required") == "act-now"
    # The same evidence on another kind: the general rule (no location: review).
    assert tier("breaking", "high", TARGET_FAILURE, None, "target-build-failed") == "review"
    assert tier("breaking", "medium", TARGET_FAILURE, None, "module-required") == "review"
    manifest_only = [TARGET_FAILURE[1]]  # no evidence of the target build's own failure
    assert tier("breaking", "high", manifest_only, None, "module-required") == "review"
