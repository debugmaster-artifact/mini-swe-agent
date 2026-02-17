"""Operation history tree structure for LLMIDEAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from debugmaster.agents.llm_ide.code_context_manager import CodeChunk


class ActionProperty(str, Enum):
    EXPLOITATIVE = "exploitative"
    EXPLORATORY = "exploratory"


@dataclass
class ActionObservation:
    action: str
    observation: str


@dataclass
class OperationNode:
    thoughts: str = ""
    action: str = ""
    action_property: ActionProperty | None = None
    observations: list[ActionObservation] = field(default_factory=list)
    summary: str = ""
    valid: bool | None = None
    lessons: str = ""
    dead_path_summaries: list[str] = field(default_factory=list)
    code_chunks: list[CodeChunk] = field(default_factory=list)
    tool_status: dict[str, Any] = field(default_factory=dict)
    code_change: str = ""
    dead_path: bool = False
    invalid_ops: list[OperationNode] = field(default_factory=list)
    parent: OperationNode | None = field(default=None, repr=False)
    children: list[OperationNode] = field(default_factory=list)


class ActionManager:
    def __init__(self, max_invalid: int = 3):
        self._sentinel = OperationNode()
        self.root = self._sentinel
        self.current = self._sentinel
        self.max_invalid = max_invalid
        self._all_nodes: list[OperationNode] = []
        self._temp_node: OperationNode | None = None

    @property
    def has_pending_node(self) -> bool:
        return self._temp_node is not None

    @property
    def has_real_current(self) -> bool:
        return self.current is not self._sentinel

    @property
    def active_node(self) -> OperationNode | None:
        return self._temp_node or (self.current if self.has_real_current else None)

    def create_temp_node(self, thoughts: str, action: str, action_property: ActionProperty | None) -> OperationNode:
        node = OperationNode(thoughts=thoughts, action=action, action_property=action_property)
        self._temp_node = node
        self._all_nodes.append(node)
        return node

    def commit_admissible(self):
        """Link temp node as child of current, make it current."""
        node = self._temp_node
        if not node:
            return
        node.parent = self.current
        self.current.children.append(node)
        self.current = node
        self._temp_node = None

    def commit_invalid(self) -> bool:
        """Add temp node to current's invalid_ops list. Returns True if overflow (dead-end)."""
        node = self._temp_node
        if not node:
            return False
        node.parent = self.current
        self.current.invalid_ops.append(node)
        self._temp_node = None
        return len(self.current.invalid_ops) >= self.max_invalid

    def set_observation(self, observations: list[ActionObservation]):
        if self._temp_node:
            self._temp_node.observations = observations

    def set_reflection(self, valid: bool, lessons: str, summary: str = ""):
        if self._temp_node:
            self._temp_node.valid = valid
            self._temp_node.lessons = lessons
            self._temp_node.summary = summary

    def find_backtrack_target(self) -> OperationNode | None:
        """Walk up from current's parent to find closest EXPLORATORY ancestor."""
        node = self.current.parent
        while node and node is not self._sentinel:
            if node.action_property == ActionProperty.EXPLORATORY:
                return node
            node = node.parent
        return None

    def backtrack_to(self, target: OperationNode, dead_path_summary: str):
        """Mark the child on the dead path, append summary to target, set current to target."""
        # Walk from current up to find target's direct child on the dead path
        node = self.current
        while node and node.parent is not target:
            node = node.parent
        if node:
            node.dead_path = True
        target.dead_path_summaries.append(dead_path_summary)
        self.current = target

    def get_dead_path(self, from_node: OperationNode) -> list[OperationNode]:
        """Collect path from from_node to current (inclusive)."""
        if self.current is self._sentinel:
            return []
        path: list[OperationNode] = []
        node = self.current
        while node and node is not from_node:
            path.append(node)
            node = node.parent
        if node is from_node:
            path.append(from_node)
        path.reverse()
        return path

    def get_path_to(self, target: OperationNode) -> list[OperationNode]:
        """Collect path from root to target (inclusive), excluding sentinel."""
        path: list[OperationNode] = []
        node = target
        while node and node is not self._sentinel:
            path.append(node)
            node = node.parent
        path.reverse()
        return path

    def get_path_from_root_to_current(self) -> list[OperationNode]:
        if self.current is self._sentinel:
            return []
        return self.get_path_to(self.current)

    def get_reasoning_chain(self) -> list[OperationNode]:
        live_roots = [c for c in self._sentinel.children if not c.dead_path]
        if not live_roots:
            return []
        chain: list[OperationNode] = []
        node = live_roots[0]
        while node:
            chain.append(node)
            live_children = [c for c in node.children if not c.dead_path]
            children_with_children = [c for c in live_children if c.children]
            if children_with_children:
                node = children_with_children[0]
            elif live_children:
                node = live_children[-1]
            else:
                break
        if self.has_real_current and self.current not in chain:
            chain.append(self.current)
        return chain

    def get_rejected_actions(self) -> list[OperationNode]:
        """Collect all invalid_ops nodes from root to current."""
        rejected: list[OperationNode] = []
        for node in self.get_path_from_root_to_current():
            rejected.extend(node.invalid_ops)
        return rejected
