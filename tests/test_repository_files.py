"""仓库安全遍历在异常挂载和符号链接场景下的回归测试。"""

import os
from pathlib import Path

import pytest

import app.repository_files as repository_files
from app.code_tools import search_code
from app.document_chunks import build_repository_chunks
from app.php_index import index_repository
from app.repository_files import iter_repository_files


def test_repository_scanners_skip_invalid_directory_and_keep_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证单个目录触发 EINVAL 时，PHP、文档和文本索引仍返回其他源码。

    参数:
        - `tmp_path`：pytest 隔离临时目录，用于构造测试项目。
        - `monkeypatch`：pytest 运行期替换工具，用于模拟 Docker 挂载目录返回
          `OSError(22, "Invalid argument")`。
    """
    project = tmp_path / "waky6"
    source_dir = project / "php" / "domain"
    source_dir.mkdir(parents=True)
    broken_dir = project / "php" / "tools" / "conf"
    broken_dir.mkdir(parents=True)
    (source_dir / "WithdrawalOrderCreateService.php").write_text(
        "<?php\nclass WithdrawalOrderCreateService {}\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text("official withdrawal daily limit", encoding="utf-8")

    real_scandir = os.scandir

    def fail_abnormal_mount(path: object) -> object:
        """模拟单个损坏挂载目录，其他目录仍委托给真实 `scandir`。

        参数:
            - `path`：`os.walk` 当前准备读取的目录路径，通常是字符串或文件描述符。

        Returns:
            真实 `os.scandir` 返回的目录迭代器；目标异常目录则抛出与线上任务一致的
            `EINVAL`，用于证明调用方会跳过并继续扫描。
        """
        if Path(path).name == "conf":
            raise OSError(22, "Invalid argument", str(path))
        return real_scandir(path)

    monkeypatch.setattr(repository_files.os, "scandir", fail_abnormal_mount)
    monkeypatch.setattr("app.code_tools.shutil.which", lambda _name: None)

    index = index_repository(str(tmp_path), "waky6")
    chunks = build_repository_chunks(str(tmp_path), "waky6", max_chars=200)
    search = search_code(str(tmp_path), "waky6", "WithdrawalOrderCreateService")

    assert "WithdrawalOrderCreateService" in {
        item["symbol_name"] for item in index["symbols"]
    }
    assert "php/domain/WithdrawalOrderCreateService.php" in {
        item["file_path"] for item in chunks
    }
    assert "README.md" in {item["file_path"] for item in chunks}
    assert search["matches"][0]["file"] == "php/domain/WithdrawalOrderCreateService.php"


def test_repository_files_do_not_follow_links_outside_project(tmp_path: Path) -> None:
    """验证安全遍历不会读取项目外文件，也不会陷入目录链接循环。

    参数:
        - `tmp_path`：pytest 隔离临时目录，用于同时构造项目内源码和项目外秘密文件。
    """
    project = tmp_path / "waky6"
    project.mkdir()
    outside = tmp_path / "outside.php"
    outside.write_text("<?php class SecretOutsideProject {}", encoding="utf-8")
    (project / "safe.php").write_text("<?php class SafeFile {}", encoding="utf-8")
    try:
        (project / "outside.php").symlink_to(outside)
        (project / "loop").symlink_to(project, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持符号链接测试: {exc}")

    files = list(iter_repository_files(project, {".php"}))

    assert [path.name for path in files] == ["safe.php"]
