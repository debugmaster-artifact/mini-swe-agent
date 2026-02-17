"""Tests for ActionManager operation history tree."""

import pytest

from debugmaster.agents.llm_ide.action_manager import (
    ActionManager,
    ActionObservation,
    ActionProperty,
    OperationNode,
)


def _make_manager(max_invalid: int = 3) -> ActionManager:
    return ActionManager(max_invalid=max_invalid)


def _commit_admissible(
    am: ActionManager,
    thoughts: str = "",
    action: str = "",
    prop: ActionProperty | None = None,
) -> OperationNode:
    node = am.create_temp_node(thoughts, action, prop)
    am.commit_admissible()
    return node


# ── OperationNode defaults ───────────────────────────────────────────

def test_code_change_default_and_set():
    node = OperationNode()
    assert node.code_change == ""
    node.code_change = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
    assert "new" in node.code_change


# ── Initial state ────────────────────────────────────────────────────

def test_initial_state():
    am = _make_manager()
    assert am.root is am.current  # both point to sentinel
    assert am.has_pending_node is False
    assert am.active_node is None


# ── create_temp_node ─────────────────────────────────────────────────

def test_create_temp_node():
    am = _make_manager()
    node = am.create_temp_node("t", "a", ActionProperty.EXPLOITATIVE)
    assert am.has_pending_node is True
    assert am.active_node is node
    assert node in am._all_nodes
    assert node.thoughts == "t"
    assert node.action == "a"
    assert node.action_property == ActionProperty.EXPLOITATIVE


# ── set_observation / set_reflection ─────────────────────────────────

def test_set_observation_and_reflection():
    am = _make_manager()
    am.create_temp_node("t", "a", None)
    am.set_observation([ActionObservation("cmd", "obs")])
    am.set_reflection(True, "lesson1")
    assert am._temp_node.observations == [ActionObservation("cmd", "obs")]
    assert am._temp_node.valid is True
    assert am._temp_node.lessons == "lesson1"
    assert am._temp_node.summary == ""


def test_set_reflection_with_summary():
    am = _make_manager()
    am.create_temp_node("t", "a", None)
    am.set_observation([ActionObservation("cmd", "obs")])
    am.set_reflection(True, "lesson1", summary="short summary")
    assert am._temp_node.summary == "short summary"
    assert am._temp_node.valid is True
    assert am._temp_node.lessons == "lesson1"


def test_set_observation_noop_without_temp():
    am = _make_manager()
    am.set_observation([ActionObservation("cmd", "obs")])
    am.set_reflection(False, "x")
    assert am._temp_node is None


# ── commit_admissible ────────────────────────────────────────────────

def test_commit_admissible_first_becomes_child_of_root():
    am = _make_manager()
    node = am.create_temp_node("t", "a", None)
    am.commit_admissible()
    assert node.parent is am.root
    assert am.current is node
    assert am.has_pending_node is False


def test_commit_admissible_chain():
    am = _make_manager()
    a = _commit_admissible(am, "a")
    b = _commit_admissible(am, "b")
    c = _commit_admissible(am, "c")
    assert a.parent is am.root
    assert am.current is c
    assert b.parent is a
    assert c.parent is b
    assert a.children == [b]
    assert b.children == [c]


def test_commit_admissible_noop_without_temp():
    am = _make_manager()
    am.commit_admissible()
    assert not am.root.children


# ── commit_invalid ───────────────────────────────────────────────────

def test_commit_invalid_under_threshold():
    am = _make_manager(max_invalid=3)
    _commit_admissible(am)
    am.create_temp_node("np1", "a", None)
    assert am.commit_invalid() is False
    assert len(am.current.invalid_ops) == 1
    assert am.has_pending_node is False


def test_commit_invalid_overflow():
    am = _make_manager(max_invalid=2)
    root = _commit_admissible(am)
    am.create_temp_node("np1", "a", None)
    assert am.commit_invalid() is False
    am.create_temp_node("np2", "a", None)
    assert am.commit_invalid() is True
    assert len(root.invalid_ops) == 2


def test_commit_invalid_parent_link():
    am = _make_manager()
    root = _commit_admissible(am)
    am.create_temp_node("np", "a", None)
    am.commit_invalid()
    assert root.invalid_ops[0].parent is root


def test_commit_invalid_attaches_to_sentinel():
    am = _make_manager()
    am.create_temp_node("np", "a", None)
    assert am.commit_invalid() is False
    assert len(am.root.invalid_ops) == 1


def test_commit_invalid_noop_without_temp():
    am = _make_manager()
    _commit_admissible(am)
    assert am.commit_invalid() is False


# ── find_backtrack_target ────────────────────────────────────────────

def test_find_backtrack_target_none_when_empty():
    assert _make_manager().find_backtrack_target() is None


def test_find_backtrack_target_finds_non_det_ancestor():
    am = _make_manager()
    a = _commit_admissible(am, prop=ActionProperty.EXPLORATORY)
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    assert am.find_backtrack_target() is a


def test_find_backtrack_target_none_when_all_deterministic():
    am = _make_manager()
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    assert am.find_backtrack_target() is None


def test_find_backtrack_target_skips_deterministic():
    am = _make_manager()
    _commit_admissible(am, prop=ActionProperty.EXPLORATORY)
    nd2 = _commit_admissible(am, prop=ActionProperty.EXPLORATORY)
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    assert am.find_backtrack_target() is nd2


# ── backtrack_to ─────────────────────────────────────────────────────

def test_backtrack_to():
    am = _make_manager()
    a = _commit_admissible(am, prop=ActionProperty.EXPLORATORY)
    b = _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    _commit_admissible(am, prop=ActionProperty.EXPLOITATIVE)
    am.backtrack_to(a, "dead path summary")
    assert am.current is a
    assert b.dead_path is True
    assert a.dead_path_summaries == ["dead path summary"]


# ── get_dead_path ────────────────────────────────────────────────────

def test_get_dead_path():
    am = _make_manager()
    a = _commit_admissible(am)
    b = _commit_admissible(am)
    c = _commit_admissible(am)
    assert am.get_dead_path(a) == [a, b, c]


def test_get_dead_path_empty_when_at_sentinel():
    am = _make_manager()
    assert am.get_dead_path(OperationNode()) == []


# ── get_path_to / get_path_from_root_to_current ─────────────────────

def test_get_path_to():
    am = _make_manager()
    a = _commit_admissible(am)
    b = _commit_admissible(am)
    c = _commit_admissible(am)
    assert am.get_path_to(c) == [a, b, c]


def test_get_path_from_root_to_current():
    am = _make_manager()
    a = _commit_admissible(am)
    b = _commit_admissible(am)
    assert am.get_path_from_root_to_current() == [a, b]


def test_get_path_from_root_to_current_empty_at_sentinel():
    assert _make_manager().get_path_from_root_to_current() == []


# ── get_reasoning_chain ──────────────────────────────────────────────

def test_reasoning_chain_empty():
    assert _make_manager().get_reasoning_chain() == []


def test_reasoning_chain_linear():
    am = _make_manager()
    a = _commit_admissible(am)
    b = _commit_admissible(am)
    c = _commit_admissible(am)
    assert am.get_reasoning_chain() == [a, b, c]


def test_reasoning_chain_excludes_dead_paths():
    am = _make_manager()
    a = _commit_admissible(am, prop=ActionProperty.EXPLORATORY)
    b = _commit_admissible(am)
    c = _commit_admissible(am)
    am.backtrack_to(a, "dead")
    d = _commit_admissible(am, thoughts="retry")
    # b is marked dead_path, chain should go a → d
    assert am.get_reasoning_chain() == [a, d]


def test_reasoning_chain_appends_current_if_missing():
    am = _make_manager()
    a = _commit_admissible(am)
    b = _commit_admissible(am)
    am.backtrack_to(a, "dead")
    # current is a, but b is dead_path → chain=[a], current already in chain
    assert am.get_reasoning_chain() == [a]


# ── get_rejected_actions ─────────────────────────────────────────────

def test_get_rejected_actions_empty():
    assert _make_manager().get_rejected_actions() == []


def test_get_rejected_actions_collects_from_root_to_current():
    am = _make_manager()
    n1 = _commit_admissible(am, thoughts="t1")
    # add an invalid node
    am.create_temp_node("np", "a", None)
    am.commit_invalid()
    _commit_admissible(am, thoughts="t2")
    rejected = am.get_rejected_actions()
    assert len(rejected) == 1
    assert rejected[0].thoughts == "np"
    assert rejected[0].action == "a"


def test_get_rejected_actions_empty_when_no_invalid():
    am = _make_manager()
    _commit_admissible(am)
    _commit_admissible(am)
    assert am.get_rejected_actions() == []


# ── Complex: full backtrack-and-retry ────────────────────────────────

def test_full_backtrack_and_retry():
    am = _make_manager()
    a = _commit_admissible(am, thoughts="a", prop=ActionProperty.EXPLORATORY)
    b = _commit_admissible(am, thoughts="b", prop=ActionProperty.EXPLOITATIVE)
    c = _commit_admissible(am, thoughts="c", prop=ActionProperty.EXPLOITATIVE)
    # c is dead-end, backtrack to a
    am.backtrack_to(a, "path A→B→C was a dead end")
    assert am.current is a
    assert b.dead_path is True
    assert a.dead_path_summaries == ["path A→B→C was a dead end"]
    # add sibling d
    d = _commit_admissible(am, thoughts="d", prop=ActionProperty.EXPLOITATIVE)
    assert d.parent is a
    assert am.current is d
    assert am.get_reasoning_chain() == [a, d]


def test_multiple_dead_paths_from_same_node():
    am = _make_manager()
    a = _commit_admissible(am, thoughts="a", prop=ActionProperty.EXPLORATORY)
    # first dead path
    b = _commit_admissible(am, thoughts="b")
    am.backtrack_to(a, "dead path 1")
    assert b.dead_path is True
    # second dead path
    c = _commit_admissible(am, thoughts="c")
    am.backtrack_to(a, "dead path 2")
    assert c.dead_path is True
    assert a.dead_path_summaries == ["dead path 1", "dead path 2"]
    # final retry
    d = _commit_admissible(am, thoughts="d")
    assert am.get_reasoning_chain() == [a, d]
