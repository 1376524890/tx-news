# Input: scripts/start_local.sh / scripts/stop_local.sh
# Output: 基本 --help 行为断言（不触网/不依赖 Docker/venv）
# Pos: 运维脚本最小回归测试（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import subprocess


def test_start_local_help() -> None:
    p = subprocess.run(
        ["bash", "scripts/start_local.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "Usage:" in p.stdout
    assert "--web-dev" in p.stdout
    assert "--no-web-dev" in p.stdout


def test_stop_local_help() -> None:
    p = subprocess.run(
        ["bash", "scripts/stop_local.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "Usage:" in p.stdout
    assert "--keep-infra" in p.stdout
