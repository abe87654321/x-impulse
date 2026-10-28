"""已注册的连接器实现。

新增连接器时在此登记，无需改 test_connector.py。

格式：
    CONNECTORS = {
        "sharepoint": {
            "connector": lambda sandbox: SharePointConnector(client=sandbox.client),
            "sandbox":   SharePointSandbox,
        },
    }
"""

from __future__ import annotations

CONNECTORS: dict[str, dict] = {}
