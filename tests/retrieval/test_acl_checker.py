"""检索接缝上的权限求值 —— 断开继承必须在【链上全部对象】生效（I-2）。

这是 TD-03 B-1 的回归测试：只查文档自身的省略写法，会在这里变红。
"""

from __future__ import annotations

from kb.retrieval.retriever import AclPermissionChecker
from tests.retrieval.fakes import FakeAclRepository, make_ctx, row

CTX = make_ctx("alice")
MEMBERSHIPS = frozenset({"grp_eng"})

HIERARCHY = {
    "doc_1": [
        ("doc_1", "document", "fld_1"),
        ("fld_1", "folder", "src_1"),
        ("src_1", "source", None),
    ]
}


async def _allowed(acls, broken) -> bool:
    checker = AclPermissionChecker(FakeAclRepository(HIERARCHY, acls, broken))
    return await checker.is_allowed(CTX, "doc_1", MEMBERSHIPS)


async def test_folder_inheritance_break_shields_ancestor_deny():
    # 目录断开继承：目录自身的 allow 仍适用，源上的 deny 不再适用。
    acls = {
        "fld_1": [row("fld_1", object_type="folder", principal_id="grp_eng")],
        "src_1": [row("src_1", object_type="source", principal_id="grp_eng", effect="deny")],
    }
    assert await _allowed(acls, frozenset({"fld_1"})) is True


async def test_only_checking_document_would_leak_ancestor_deny():
    # 若实现只把 doc_1 放进 broken 查询，目录的断开会被忽略，源 deny 泄漏。
    acls = {
        "fld_1": [row("fld_1", object_type="folder", principal_id="grp_eng")],
        "src_1": [row("src_1", object_type="source", principal_id="grp_eng", effect="deny")],
    }
    checker = AclPermissionChecker(FakeAclRepository(HIERARCHY, acls, broken=frozenset({"fld_1"})))
    # 这条断言本身不证明实现细节，但配合下面的"无断开"对照才完整。
    assert await checker.is_allowed(CTX, "doc_1", MEMBERSHIPS) is True

    no_break = AclPermissionChecker(FakeAclRepository(HIERARCHY, acls, frozenset()))
    assert await no_break.is_allowed(CTX, "doc_1", MEMBERSHIPS) is False


async def test_document_inheritance_break_shields_ancestor_deny():
    hierarchy = {
        "doc_1": [
            ("doc_1", "document", "fld_1"),
            ("fld_1", "folder", "src_1"),
            ("src_1", "source", None),
        ]
    }
    acls = {
        "doc_1": [row("doc_1", object_type="document", principal_id="grp_eng")],
        "fld_1": [row("fld_1", object_type="folder", principal_id="grp_eng", effect="deny")],
    }
    checker = AclPermissionChecker(FakeAclRepository(hierarchy, acls, frozenset({"doc_1"})))
    assert await checker.is_allowed(CTX, "doc_1", MEMBERSHIPS) is True


async def test_default_deny_without_allow():
    assert await _allowed({}, frozenset()) is False


async def test_document_allow_without_break():
    acls = {
        "doc_1": [row("doc_1", object_type="document", principal_id="grp_eng")],
    }
    assert await _allowed(acls, frozenset()) is True
