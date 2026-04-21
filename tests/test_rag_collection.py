from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("chromadb")
pytest.importorskip("openai")

import rag


def test_collect_knowledge_files_includes_md_and_txt(tmp_path):
    (tmp_path / "a.md").write_text("# A\nhello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain text", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.md").write_text("# C\nworld", encoding="utf-8")
    (nested / "d.json").write_text("{}", encoding="utf-8")

    files = rag._collect_knowledge_files(str(tmp_path))
    rel = {str(Path(f).relative_to(tmp_path)) for f in files}

    assert "a.md" in rel
    assert "b.txt" in rel
    assert "nested/c.md" in rel
    assert "nested/d.json" not in rel
