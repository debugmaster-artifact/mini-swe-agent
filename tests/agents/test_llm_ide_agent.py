import subprocess
from pathlib import Path
from types import SimpleNamespace

from debugmaster.agents.llm_ide.llm_ide_agent import LLMIDEAgent
from debugmaster.models.test_models import DeterministicModel


class DummyEnvironment:
    def __init__(self, target: str, content: str, *, reproduction_complete: bool, git_diff: str = ""):
        self.config = SimpleNamespace(
            cwd="/testbed",
            reproduction_complete=reproduction_complete,
            reproduction_script={"target": target},
        )
        self._files = {target: content}
        self._git_diff = git_diff
        self.executed_commands: list[str] = []

    def get_template_vars(self) -> dict:
        return {
            "cwd": self.config.cwd,
            "reproduction_complete": self.config.reproduction_complete,
            "reproduction_script": self.config.reproduction_script,
            "installed_tools": [],
        }

    def get_file(self, file_path: str) -> str:
        return self._files[file_path]

    def execute(self, command: str, cwd: str = "") -> dict:
        self.executed_commands.append(command)
        if command.strip() == "git --no-pager diff HEAD":
            return {"output": self._git_diff, "returncode": 0}
        return {"output": "", "returncode": 0}


def _make_agent(env: DummyEnvironment) -> LLMIDEAgent:
    return LLMIDEAgent(
        model=DeterministicModel(outputs=[]),
        env=env,
        system_template="",
        instance_template="",
        timeout_template="",
        format_error_template="",
        action_observation_template="",
    )


def test_init_code_context_preloads_reproduction_script():
    target = "/testbed/issue_reproduction.py"
    agent = _make_agent(DummyEnvironment(target, "a = 1\nb = 2\nprint(a + b)\n", reproduction_complete=True))

    agent._init_code_context()

    chunks = agent._collect_code_context_chunks()
    assert len(chunks) == 1
    assert chunks[0].file_path == target
    assert chunks[0].lines == [1, 2, 3]
    assert not chunks[0].whole_function


def test_builtin_tool_returns_none_when_not_initialized():
    agent = _make_agent(DummyEnvironment("/testbed/a.py", "print('x')\n", reproduction_complete=False))
    assert agent._run_builtin_tool("get-nearby-code-context /testbed/a.py 1") is None


def test_builtin_tool_returns_none_for_unrecognized_command():
    agent = _make_agent(DummyEnvironment("/testbed/a.py", "print('x')\n", reproduction_complete=False))
    agent._init_code_context()
    agent._builtin_tools = agent._init_builtin_tools()
    assert agent._run_builtin_tool("unknown-tool /testbed/a.py 1") is None


def test_get_nearby_code_context_reports_function_and_tracks_chunk():
    source = "def add(a, b):\n    return a + b\n"
    agent = _make_agent(DummyEnvironment("/testbed/math.py", source, reproduction_complete=False))
    agent._init_code_context()
    agent._builtin_tools = agent._init_builtin_tools()
    agent.action_manager.create_temp_node("thought", "action", None)

    result = agent._run_builtin_tool("get-nearby-code-context /testbed/math.py 2")
    assert result == {
        "output": "Function add in file /testbed/math.py is added into the code context.",
        "returncode": 0,
    }
    assert len(agent.action_manager.active_node.code_chunks) == 1
    assert agent.action_manager.active_node.code_chunks[0].function == "add"
    assert agent.action_manager.active_node.code_chunks[0].whole_function


def test_get_nearby_code_context_reports_empty_file():
    agent = _make_agent(DummyEnvironment("/testbed/empty.py", "", reproduction_complete=False))
    agent._init_code_context()
    agent._builtin_tools = agent._init_builtin_tools()

    result = agent._run_builtin_tool("get-nearby-code-context /testbed/empty.py 1")
    assert result == {
        "output": "No lines found for /testbed/empty.py",
        "returncode": 0,
    }


def test_get_code_lines_returns_line_range():
    source = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n"
    agent = _make_agent(DummyEnvironment("/testbed/lines.py", source, reproduction_complete=False))
    agent._init_code_context()
    agent._builtin_tools = agent._init_builtin_tools()
    agent.action_manager.create_temp_node("thought", "action", None)

    result = agent._run_builtin_tool("get-code-lines /testbed/lines.py 2 4")
    assert result == {
        "output": "Lines 2 to 4 of file /testbed/lines.py are added into the code context.",
        "returncode": 0,
    }
    assert len(agent.action_manager.active_node.code_chunks) == 1
    assert agent.action_manager.active_node.code_chunks[0].lines == [2, 3, 4]


# ── Version control helpers ──────────────────────────────────────────


def test_get_git_diff_returns_output():
    diff = "--- a/f.py\n+++ b/f.py\n-old\n+new"
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff=diff)
    agent = _make_agent(env)
    assert agent._get_git_diff() == diff


def test_get_git_diff_returns_empty_on_failure():
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False)
    # Override execute to return non-zero
    env.execute = lambda cmd, cwd="": {"output": "error", "returncode": 1}
    agent = _make_agent(env)
    assert agent._get_git_diff() == ""


def test_execute_actions_captures_diff():
    diff = "--- a/f.py\n+++ b/f.py\n-old\n+new"
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff=diff)
    agent = _make_agent(env)
    agent._init_code_context()
    agent.action_manager.create_temp_node("t", "echo hi", None)
    observations, returncode = agent._execute_actions(["echo hi"])
    assert agent.action_manager.active_node.code_change == diff
    assert len(observations) == 1
    assert observations[0].action == "echo hi"
    assert "[returncode: 0]" in observations[0].observation


def test_sync_version_control_noop_when_diff_matches():
    diff = "--- a/f.py\n+++ b/f.py\n-old\n+new"
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff=diff)
    agent = _make_agent(env)
    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = diff
    env.executed_commands.clear()
    agent._sync_version_control()
    # Should only call git diff, no reset
    assert not any("git reset" in cmd for cmd in env.executed_commands)


def test_sync_version_control_resets_and_applies_on_mismatch():
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff="current diff")
    agent = _make_agent(env)
    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = "expected diff"
    env.executed_commands.clear()
    agent._sync_version_control()
    assert any("git reset --hard HEAD" in cmd for cmd in env.executed_commands)
    assert any("base64" in cmd for cmd in env.executed_commands)


def test_sync_version_control_resets_only_when_target_has_no_patch():
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff="some diff")
    agent = _make_agent(env)
    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = ""
    env.executed_commands.clear()
    agent._sync_version_control()
    assert any("git reset --hard HEAD" in cmd for cmd in env.executed_commands)
    # No apply patch since code_change is empty
    assert not any("base64" in cmd for cmd in env.executed_commands)


def test_sync_version_control_noop_at_sentinel():
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff="some diff")
    agent = _make_agent(env)
    env.executed_commands.clear()
    agent._sync_version_control()
    assert env.executed_commands == []


def test_sync_version_control_after_backtrack_applies_target_patch():
    """After backtracking, sync should restore the backtrack target's code_change."""
    from debugmaster.agents.llm_ide.action_manager import ActionProperty

    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff="wrong diff")
    agent = _make_agent(env)
    # Build chain: node_a (non-det) -> node_b (dead-end)
    node_a = agent.action_manager.create_temp_node("t", "a", ActionProperty.EXPLORATORY)
    agent.action_manager.commit_admissible()
    node_a.code_change = "patch-a"

    node_b = agent.action_manager.create_temp_node("t", "b", ActionProperty.EXPLOITATIVE)
    agent.action_manager.commit_admissible()
    node_b.code_change = "patch-b"

    # Backtrack to node_a
    agent.action_manager.backtrack_to(node_a, "dead path summary")
    assert agent.action_manager.current is node_a
    env.executed_commands.clear()
    agent._sync_version_control()
    # Should reset because env diff ("wrong diff") != node_a.code_change ("patch-a")
    assert any("git reset --hard HEAD" in cmd for cmd in env.executed_commands)
    assert any("base64" in cmd for cmd in env.executed_commands)


def test_sync_version_control_uses_current_node_not_parent():
    """When multiple nodes are chained, sync reads code_change from current, not its parent."""
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False, git_diff="child-patch")
    agent = _make_agent(env)

    parent = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    parent.code_change = "parent-patch"

    child = agent.action_manager.create_temp_node("t", "b", None)
    agent.action_manager.commit_admissible()
    child.code_change = "child-patch"

    env.executed_commands.clear()
    agent._sync_version_control()
    # Diff matches child's code_change, so no reset even though parent differs
    assert not any("git reset" in cmd for cmd in env.executed_commands)


def test_apply_patch_sends_correct_base64():
    """_apply_patch base64-encodes the diff and passes it to the shell."""
    import base64
    env = DummyEnvironment("/testbed/a.py", "x\n", reproduction_complete=False)
    agent = _make_agent(env)
    diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new"
    env.executed_commands.clear()
    agent._apply_patch(diff)
    assert len(env.executed_commands) == 1
    cmd = env.executed_commands[0]
    encoded = base64.b64encode(diff.encode("utf-8")).decode("ascii")
    assert encoded in cmd
    assert "git apply" in cmd


# ── Integration tests with real git repo ─────────────────────────────


class GitEnvironment:
    """Environment backed by a real git repo for integration tests."""

    def __init__(self, repo: Path):
        self.config = SimpleNamespace(
            cwd=str(repo),
            reproduction_complete=False,
            reproduction_script={"target": str(repo / "hello.py")},
        )
        self._repo = repo

    def get_template_vars(self) -> dict:
        return {
            "cwd": self.config.cwd,
            "reproduction_complete": False,
            "reproduction_script": self.config.reproduction_script,
            "installed_tools": [],
        }

    def get_file(self, file_path: str) -> str:
        return Path(file_path).read_text()

    def execute(self, command: str, cwd: str = "") -> dict:
        result = subprocess.run(
            command, shell=True, text=True,
            cwd=cwd or str(self._repo),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        return {"output": result.stdout, "returncode": result.returncode}


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda args: subprocess.run(args, cwd=str(repo), capture_output=True, check=True)
    run(["git", "init"])
    run(["git", "config", "user.email", "test@test.com"])
    run(["git", "config", "user.name", "Test"])
    (repo / "hello.py").write_text("a = 1\n")
    run(["git", "add", "."])
    run(["git", "commit", "-m", "init"])
    return repo


def test_sync_restores_file_after_divergent_edit(tmp_path):
    repo = _init_repo(tmp_path)
    agent = _make_agent(GitEnvironment(repo))

    (repo / "hello.py").write_text("a = 2\n")
    patch = agent._get_git_diff()

    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = patch

    (repo / "hello.py").write_text("a = 999\n")
    agent._sync_version_control()

    assert (repo / "hello.py").read_text() == "a = 2\n"


def test_sync_resets_to_clean_when_node_has_no_patch(tmp_path):
    repo = _init_repo(tmp_path)
    agent = _make_agent(GitEnvironment(repo))

    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = ""

    (repo / "hello.py").write_text("a = 999\n")
    agent._sync_version_control()

    assert (repo / "hello.py").read_text() == "a = 1\n"


def test_sync_noop_when_working_tree_matches_patch(tmp_path):
    repo = _init_repo(tmp_path)
    agent = _make_agent(GitEnvironment(repo))

    (repo / "hello.py").write_text("a = 2\n")
    patch = agent._get_git_diff()

    node = agent.action_manager.create_temp_node("t", "a", None)
    agent.action_manager.commit_admissible()
    node.code_change = patch

    agent._sync_version_control()

    assert (repo / "hello.py").read_text() == "a = 2\n"


def test_sync_after_backtrack_restores_ancestor_patch(tmp_path):
    from debugmaster.agents.llm_ide.action_manager import ActionProperty

    repo = _init_repo(tmp_path)
    agent = _make_agent(GitEnvironment(repo))

    (repo / "hello.py").write_text("a = 2\n")
    patch_a = agent._get_git_diff()
    node_a = agent.action_manager.create_temp_node("t", "a", ActionProperty.EXPLORATORY)
    agent.action_manager.commit_admissible()
    node_a.code_change = patch_a

    (repo / "hello.py").write_text("a = 3\n")
    patch_b = agent._get_git_diff()
    node_b = agent.action_manager.create_temp_node("t", "b", ActionProperty.EXPLOITATIVE)
    agent.action_manager.commit_admissible()
    node_b.code_change = patch_b

    agent.action_manager.backtrack_to(node_a, "dead path summary")
    agent._sync_version_control()

    assert (repo / "hello.py").read_text() == "a = 2\n"


# ── Action parsing ───────────────────────────────────────────────────


import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<action>ls -la</action>", ["ls -la"]),
        ("<action>`ls -la`</action>", ["ls -la"]),
        ("<action>```\nls -la\n```</action>", ["ls -la"]),
        ("<action>```bash\nls -la\n```</action>", ["ls -la"]),
        ("<action>\n```\nls -la\n```\n</action>", ["ls -la"]),
        ("<action>`  ls -la  `</action>", ["ls -la"]),
    ],
)
def test_parse_actions_strips_backticks(raw, expected):
    agent = _make_agent(DummyEnvironment("f.py", "", reproduction_complete=False))
    assert agent._parse_actions(raw) == expected
