import pytest

from db.schema import init_db
from db.store import query_code_index
from orchestration.indexer import detect_language, index_file


@pytest.mark.asyncio
async def test_index_file_extracts_python_symbols_and_refs():
    db = await init_db(":memory:")
    try:
        content = (
            "import os\n"
            "from app.services import auth\n\n"
            "class UserService:\n"
            "    pass\n\n"
            "def login(username: str) -> bool:\n"
            "    return bool(username)\n"
        )
        stats = await index_file(
            db,
            project_id="p1",
            path="backend/auth/service.py",
            content=content,
        )
        assert stats["symbols"] >= 2
        assert stats["refs"] >= 2

        rows = await query_code_index(db, project_id="p1", terms=["login"], top_k=5)
        assert rows
        assert rows[0]["path"] == "backend/auth/service.py"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_index_file_extracts_typescript_and_file_fallback():
    db = await init_db(":memory:")
    try:
        content = (
            "import { api } from './api'\n"
            "export function showPopup() { return api(); }\n"
            "export class PopupController {}\n"
        )
        stats = await index_file(
            db,
            project_id="p2",
            path="frontend/popup.ts",
            content=content,
        )
        assert stats["symbols"] >= 2
        assert stats["refs"] >= 1

        by_symbol = await query_code_index(db, project_id="p2", terms=["showPopup"], top_k=5)
        assert by_symbol
        assert by_symbol[0]["symbol"] == "showPopup"

        by_file = await query_code_index(db, project_id="p2", terms=["popup.ts"], top_k=5)
        assert by_file
        assert by_file[0]["path"] == "frontend/popup.ts"
    finally:
        await db.close()


def test_detect_language_basic():
    assert detect_language("a.py") == "python"
    assert detect_language("a.tsx") == "typescript"
    assert detect_language("a.js") == "javascript"
    assert detect_language("README.md") == ""
