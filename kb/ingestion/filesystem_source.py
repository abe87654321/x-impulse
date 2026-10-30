"""本地文件系统数据源 —— 供演示与联调。

只读 TXT/Markdown；ACL 统一 allow everyone（单租户内部演示用）。
生产接入时应改用真实的 POSIX/NTFS ACL，并按目录生成 container_id。

external_id 用相对路径：**目录内重命名会改变 ID**，这是本演示实现的已知限制。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from kb.ingestion.contract import AclEntry, AclSet
from kb.ingestion.local_folder import FolderEntry

_SUFFIX_MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


class FilesystemFolderSource:
    def __init__(self, root: str | Path, *, source_id: str = "local") -> None:
        self._root = Path(root)
        self._source_id = source_id
        if not self._root.is_dir():
            raise ValueError(f"目录不存在：{self._root}")

    @property
    def source_id(self) -> str:
        return self._source_id

    def now(self) -> datetime:
        return datetime.now(UTC)

    def entries(self) -> tuple[FolderEntry, ...]:
        entries: list[FolderEntry] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SUFFIX_MIME:
                continue
            stat = path.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            relative = path.relative_to(self._root).as_posix()
            entries.append(
                FolderEntry(
                    external_id=relative,
                    name=relative,
                    container_id=path.parent.relative_to(self._root).as_posix()
                    if path.parent != self._root
                    else None,
                    version=str(int(stat.st_mtime)),
                    updated_at=updated_at,
                    size_bytes=stat.st_size,
                    mime_type=_SUFFIX_MIME[path.suffix.lower()],
                    content=path.read_bytes(),
                    acl=AclSet(
                        entries=(
                            AclEntry(
                                principal_type="everyone",
                                principal_id="*",
                                effect="allow",
                                scope_level="source",
                            ),
                        ),
                        inherited_from=None,
                        inheritance_broken=False,
                        fetched_at=self.now(),
                    ),
                )
            )
        return tuple(entries)


__all__ = ["FilesystemFolderSource"]
