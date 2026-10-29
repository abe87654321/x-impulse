"""Agent 登记表 —— TD-03 §6。

显式注入，不得做成模块级全局：求值结果不能依赖进程历史。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    id: str
    granted_scope: frozenset[str]


class AgentRegistry:
    """已登记 Agent 及其被授予的能力范围。"""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent_id: str, *, granted_scope: set[str]) -> None:
        self._agents[agent_id] = Agent(id=agent_id, granted_scope=frozenset(granted_scope))

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
