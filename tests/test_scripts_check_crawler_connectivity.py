# Input: scripts/check_crawler_connectivity.sh + 临时 sources 文件
# Output: dry-run 模式下的基本行为断言（不触网/不依赖 Docker）
# Pos: 运维脚本的最小回归测试（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import subprocess
from pathlib import Path


def test_check_crawler_connectivity_dry_run(tmp_path: Path) -> None:
    sources = tmp_path / "sources.txt"
    sources.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "https://example.com/",
                "https://news.qq.com/ch/finance",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    p = subprocess.run(
        [
            "bash",
            "scripts/check_crawler_connectivity.sh",
            "--dry-run",
            "--sources",
            str(sources),
            "--limit",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "dry-run=1" in p.stdout
    assert f"sources_file={sources}" in p.stdout
    assert "https://example.com/" in p.stdout

