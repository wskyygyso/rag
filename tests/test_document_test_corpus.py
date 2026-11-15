"""文档测试语料生成与分块回归测试。

测试只使用临时目录和离线哈希向量，不访问真实仓库、数据库或外部模型；
重点验证语料可重复生成、旧语料清理边界，以及 Markdown 文档能产生合法的
文档分块，供索引和检索链路复用。
"""

import hashlib
from pathlib import Path

import pytest

from app.document_chunks import build_repository_chunks
from scripts.generate_document_test_corpus import (
    CORPUS_MARKER,
    DOCUMENTS,
    cleanup_stale_documents,
    generate_corpus,
)


def _digest_directory(root: Path) -> str:
    """计算目录内全部文件内容与相对路径的稳定摘要。

    参数:
        - `root`：待计算摘要的目录，类型为 `Path`。

    Returns:
        十六进制摘要字符串；文件顺序按相对路径排序，保证跨平台一致。
    """
    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        hasher.update(str(path.relative_to(root)).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def test_generate_corpus_is_deterministic_and_marked(tmp_path: Path) -> None:
    """验证语料数量固定、每篇带生成标记且重复生成内容一致。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。

    返回:
        无返回值。断言文档数量与清单一致，重复生成后目录摘要不变。
    """
    written = generate_corpus(str(tmp_path), "waky3")
    assert len(written) == len(DOCUMENTS)
    for path in written:
        assert path.read_text(encoding="utf-8").startswith(CORPUS_MARKER)
    first = _digest_directory(tmp_path / "waky3")
    generate_corpus(str(tmp_path), "waky3")
    assert _digest_directory(tmp_path / "waky3") == first


def test_generate_corpus_rejects_unsafe_project_name(tmp_path: Path) -> None:
    """验证项目名不能越界写入其他目录。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。

    返回:
        无返回值。断言路径分隔符和上跳片段都被拒绝。
    """
    for name in ("", "a/b", "../escape"):
        with pytest.raises(ValueError):
            generate_corpus(str(tmp_path), name)


def test_cleanup_only_removes_marked_stale_documents(tmp_path: Path) -> None:
    """验证清理只删除带标记且不在当前清单中的旧文档。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。

    返回:
        无返回值。人工文档和当前清单文档保留，旧生成文档被删除。
    """
    generate_corpus(str(tmp_path), "waky3")
    stale = tmp_path / "waky3" / "stale.md"
    stale.write_text(f"{CORPUS_MARKER}\n# 旧文档\n", encoding="utf-8")
    manual = tmp_path / "waky3" / "manual.md"
    manual.write_text("# 人工文档\n", encoding="utf-8")
    removed = cleanup_stale_documents(str(tmp_path), "waky3")
    assert removed == [stale]
    assert not stale.exists()
    assert manual.exists()
    assert (tmp_path / "waky3" / "README.md").exists()


def test_corpus_builds_document_chunks(tmp_path: Path) -> None:
    """验证测试语料可被文档索引识别为文档分块。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。

    返回:
        无返回值。断言所有分块来源为 document，文件覆盖全部语料，
        行号范围合法且分块长度不超过配置上限。
    """
    generate_corpus(str(tmp_path), "waky3")
    chunks = build_repository_chunks(str(tmp_path), "waky3", max_chars=600, embed=False)
    expected_files = {item[0] for item in DOCUMENTS}
    assert chunks
    assert {chunk["source_type"] for chunk in chunks} == {"document"}
    assert {chunk["file_path"] for chunk in chunks} == expected_files
    for chunk in chunks:
        # 文档分块的稳定来源标识由“文件路径:起止行”组成，便于定位与审计。
        assert chunk["source_id"].startswith(chunk["file_path"])
        assert 1 <= chunk["line_start"] <= chunk["line_end"]
        assert len(chunk["content"]) <= 600
        assert chunk["content_hash"]


def test_corpus_contains_searchable_business_terms(tmp_path: Path) -> None:
    """验证语料覆盖中文业务关键词，可用于全文检索回归。

    参数:
        - `tmp_path`：pytest 临时目录，类型为 `Path`。

    返回:
        无返回值。断言活动、提现、设备、配置和安全关键词都出现在分块正文中。
    """
    generate_corpus(str(tmp_path), "waky3")
    chunks = build_repository_chunks(str(tmp_path), "waky3", max_chars=600, embed=False)
    text = "\n".join(chunk["content"] for chunk in chunks)
    for keyword in ("等级门槛", "钻石提现", "设备注册", "配置文件", "生产必须启用"):
        assert keyword in text
