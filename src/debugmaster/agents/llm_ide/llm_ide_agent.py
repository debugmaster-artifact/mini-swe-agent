"""LLMIDEAgent: structured memory agent that builds fresh prompts each iteration."""

import base64
import inspect
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from jinja2 import StrictUndefined, Template

from debugmaster import Environment, Model
from debugmaster.agents.llm_ide.action_manager import ActionManager, ActionObservation, ActionProperty, OperationNode
from debugmaster.agents.llm_ide.code_context_manager import CodeChunk, CodeContextManager
from debugmaster.agents.default import (
    AgentConfig,
    DefaultAgent,
    ExecutionTimeoutError,
    FormatError,
    LimitsExceeded,
    NonTerminatingException,
    Submitted,
    TerminatingException,
)
from debugmaster.environments.utils.llm_ide_tool_protocol import LLMIDEToolResponseFormat
from debugmaster.utils.log import logger


@dataclass
class BuiltInTool:
    name: str
    callable: Callable


class LLMIDEAgentConfig(AgentConfig):
    general_input: str = ""
    task_description_template: str = ""
    reproduction_result_template: str = ""
    systematic_debugging_instructions_template: str = ""
    code_context_template: str = ""
    operation_history_template: str = ""
    version_control_template: str = ""
    tool_usage_template: str = ""
    rejected_operations_template: str = ""
    reflection_instructions: str = ""
    action_instructions: str = ""
    response_format: str = ""
    incoming_operation_template: str = ""
    history_output_path: str = ""
    max_invalid: int = 3
    observation_max_length: int = 10000
    observation_length: int = 5000


class LLMIDEAgent(DefaultAgent):
    def __init__(self, model: Model, env: Environment, *, config_class: type = LLMIDEAgentConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        self.n_operations: int = 0
        self.action_manager = ActionManager(max_invalid=self.config.max_invalid)
        self.code_context_manager: CodeContextManager | None = None
        self._builtin_tools: list[BuiltInTool] = []
        self.default_code_chunks: list[CodeChunk] = []
        self.installed_tools = list(self.env.get_template_vars().get("installed_tools", []))

    # ── Version control helpers ──────────────────────────────────────

    def _get_git_diff(self) -> str:
        self.env.execute("git add -N .")
        result = self.env.execute("git --no-pager diff HEAD")
        if result.get("returncode", -1) != 0:
            return ""
        return result.get("output", "")

    def _apply_patch(self, diff: str) -> None:
        encoded = base64.b64encode(diff.encode("utf-8")).decode("ascii")
        self.env.execute(
            f"printf '%s' '{encoded}' | base64 -d > /tmp/_vc_patch.diff && "
            f"git apply --whitespace=nowarn /tmp/_vc_patch.diff ; "
            f"rm -f /tmp/_vc_patch.diff"
        )

    def _sync_version_control(self) -> None:
        if not self.action_manager.has_real_current:
            return
        node = self.action_manager.current
        current_diff = self._get_git_diff()
        if current_diff == node.code_change:
            return
        self.env.execute("git reset --hard HEAD && git clean -fd")
        if node.code_change:
            self._apply_patch(node.code_change)

    def _update_tool_status(self) -> None:
        if not self.action_manager.has_real_current:
            return
        node = self.action_manager.current
        for pkg, status in node.tool_status.items():
            for tool in self.installed_tools:
                if tool["name"] == pkg:
                    tool["status"] = status
                    break

    # ── History / IO helpers ─────────────────────────────────────────

    def _get_round_index(self) -> int:
        return self.n_operations + 1

    def _get_history_dir(self) -> Path | None:
        if not self.config.history_output_path:
            return None
        raw_instance_id = (
            str(self.extra_template_vars["instance_id"]) if "instance_id" in self.extra_template_vars else "default"
        )
        instance_id = raw_instance_id.replace("/", "__") or "default"
        history_dir = Path(self.config.history_output_path).expanduser() / instance_id
        history_dir.mkdir(parents=True, exist_ok=True)
        return history_dir

    def _format_prompt_text(self, messages: list[dict[str, Any]]) -> str:
        system_text = str(messages[0].get("content", "")) if messages else ""
        prompt_text = str(messages[1].get("content", "")) if len(messages) > 1 else ""
        return f"[system text]\n{system_text}\n\n[prompt text]\n{prompt_text}".strip()

    def _save_history_text(self, round_index: int, kind: str, text: str):
        if history_dir := self._get_history_dir():
            (history_dir / f"{round_index}_{kind}.txt").write_text(text, encoding="utf-8")

    # ── Code context ─────────────────────────────────────────────────

    def _init_builtin_tools(self) -> list[BuiltInTool]:
        tools: list[BuiltInTool] = []
        if self.code_context_manager:
            tools.append(BuiltInTool("get-nearby-code-context", self.code_context_manager.get_nearby_code_context))
            tools.append(BuiltInTool("get-code-lines", self.code_context_manager.get_code_lines))
        return tools

    def _run_builtin_tool(self, command: str) -> dict[str, Any] | None:
        stripped = command.strip()
        for tool in self._builtin_tools:
            if not stripped.startswith(tool.name):
                continue
            params_str = stripped[len(tool.name):].strip()
            parts = params_str.split()
            sig = inspect.signature(tool.callable)
            args = []
            for i, param in enumerate(sig.parameters.values()):
                if i >= len(parts):
                    break
                annotation = param.annotation
                args.append(annotation(parts[i]) if annotation is not inspect.Parameter.empty else parts[i])
            result = tool.callable(*args)
            if isinstance(result, CodeChunk):
                return self._handle_code_chunk(result)
            return result
        return None

    def _handle_code_chunk(self, chunk: CodeChunk) -> dict[str, Any]:
        if self.action_manager.active_node:
            self.action_manager.active_node.code_chunks.append(chunk)
        if chunk.whole_function:
            return {"output": f"Function {chunk.function} in file {chunk.file_path} is added into the code context.", "returncode": 0}
        if not chunk.lines:
            return {"output": f"No lines found for {chunk.file_path}", "returncode": 0}
        return {"output": f"Lines {chunk.lines[0]} to {chunk.lines[-1]} of file {chunk.file_path} are added into the code context.", "returncode": 0}

    def _get_reproduction_target(self) -> str | None:
        try:
            target = str(self.env.config.reproduction_script["target"]).strip()
            return target if self.env.config.reproduction_complete and target else None
        except (AttributeError, KeyError, TypeError):
            return None

    def _load_file_as_chunk(self, file_path: str) -> CodeChunk | None:
        content = self.code_context_manager.get_file_fn(file_path) if self.code_context_manager else ""
        line_count = len(content.splitlines())
        if line_count < 1:
            return None
        return CodeChunk(
            file_path=file_path, class_name="", function="",
            whole_function=False, lines=list(range(1, line_count + 1)),
        )

    def _init_code_context(self):
        self.code_context_manager = CodeContextManager(get_file_fn=getattr(self.env, "get_file"), cwd=self.env.config.cwd)
        self.default_code_chunks = []
        target = self._get_reproduction_target()
        if not target:
            return
        if chunk := self._load_file_as_chunk(target):
            self.default_code_chunks.append(chunk)

    def _collect_code_context_chunks(self) -> list[CodeChunk]:
        chunks = list(self.default_code_chunks)
        for node in self.action_manager.get_path_from_root_to_current():
            chunks.extend(node.code_chunks)
        return chunks

    # ── Template rendering ───────────────────────────────────────────

    def render_template(self, template: str, **kwargs) -> str:
        template_vars = self.config.model_dump() | self.env.get_template_vars() | self.model.get_template_vars()
        render_vars = template_vars | self.extra_template_vars | kwargs
        return Template(template, undefined=StrictUndefined).render(**render_vars)

    # ── Prompt building ──────────────────────────────────────────────

    def _build_system_message(self, has_incoming_op: bool) -> str:
        subtask_instructions = self.render_template(self.config.systematic_debugging_instructions_template)
        task_description = self.render_template(self.config.task_description_template, subtask_instructions=subtask_instructions)
        tool_usage = self.render_template(
            self.config.tool_usage_template, installed_tools=self.installed_tools,
        )
        parts = [
            self.render_template(
                self.config.general_input,
                task_description=task_description,
                tool_usage=tool_usage,
                incoming_op=has_incoming_op,
            ),
        ]
        if has_incoming_op:
            parts.append(self.render_template(self.config.reflection_instructions))
        parts.append(self.render_template(self.config.action_instructions))
        parts.append(self.render_template(
            self.config.response_format, incoming_op=has_incoming_op,
        ))
        return "\n\n".join(parts)

    def _build_user_message(self, has_incoming_op: bool) -> str:
        code_context_chunks = self._collect_code_context_chunks()
        code_context = (
            self.code_context_manager.render(code_context_chunks)
            if self.code_context_manager else ""
        )
        reasoning_chain = self.action_manager.get_reasoning_chain()
        rejected_actions = self.action_manager.get_rejected_actions()
        current_node = self.action_manager.current if self.action_manager.has_real_current else None
        sections = [
            self.render_template(self.config.code_context_template, code_context=code_context),
            self.render_template(self.config.rejected_operations_template, rejected_actions=rejected_actions),
            self.render_template(self.config.version_control_template, current_node=current_node),
            self.render_template(self.config.operation_history_template, reasoning_chain=reasoning_chain),
        ]
        if has_incoming_op:
            sections.append(self._render_incoming_operation())
        return "\n\n".join(sections)

    def _render_incoming_operation(self) -> str:
        temp = self.action_manager._temp_node
        observation = temp.observations if temp else []
        accessed_code = (
            self.code_context_manager.render(temp.code_chunks)
            if temp and self.code_context_manager else ""
        )
        current_change = self.action_manager.current.code_change
        temp_change = temp.code_change if temp else ""
        incoming_code_change = temp_change if temp_change != current_change else ""
        return self.render_template(
            self.config.incoming_operation_template,
            thoughts=temp.thoughts if temp else "",
            observation=observation,
            accessed_code=accessed_code,
            incoming_code_change=incoming_code_change,
        )

    # ── Response parsing ─────────────────────────────────────────────

    def _parse_tag(self, content: str, tag: str) -> str:
        match = re.search(rf"<{tag}>(.*?)</{tag}>", content, re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _strip_backticks(text: str) -> str:
        s = text.strip()
        if s.startswith("```"):
            s = re.sub(r"^```\w*\n?", "", s)
            s = re.sub(r"\n?```$", "", s)
            return s.strip()
        if s.startswith("`") and s.endswith("`"):
            return s[1:-1].strip()
        return s

    def _parse_actions(self, content: str) -> list[str]:
        actions = [self._strip_backticks(m.group(1)) for m in re.finditer(r"<action>(.*?)</action>", content, re.DOTALL)]
        return [action for action in actions if action]

    # ── Reflection processing ────────────────────────────────────────

    def _process_reflection(self, content: str, round_index: int):
        decision = self._parse_tag(content, "decision").strip().lower()
        summary = self._parse_tag(content, "summary")
        lessons = self._parse_tag(content, "lessons")
        valid = decision != "reject"
        self.action_manager.set_reflection(valid, lessons, summary)
        logger.info(f"step={round_index} phase=reflection decision={'accept' if valid else 'reject'}")

        if valid:
            self.action_manager.commit_admissible()
            self._update_tool_status()
            self._sync_version_control()
        else:
            overflow = self.action_manager.commit_invalid()
            if overflow:
                raise NotImplementedError("Dead-end handling via consecutive invalid overflow is not yet implemented.")

    # ── Action processing ────────────────────────────────────────────

    def _process_action(self, content: str, round_index: int):
        thoughts = self._parse_tag(content, "thoughts")
        actions = self._parse_actions(content)
        action = "\n".join(actions)
        property_str = self._parse_tag(content, "property").strip().lower()
        logger.info(
            f"step={round_index} phase=action property={property_str or 'none'} "
            f"actions={actions if actions else ['<none>']}"
        )

        if not actions:
            raise FormatError(self.render_template(self.config.format_error_template))

        try:
            action_property = ActionProperty(property_str)
        except ValueError:
            action_property = None

        self.action_manager.create_temp_node(thoughts, action, action_property)
        self.n_operations += 1

        observations, last_returncode = self._execute_actions(actions)
        self.action_manager.set_observation(observations)
        
        logger.info(f"step={round_index} phase=action returncode={last_returncode}")
        return observations, last_returncode

    # ── Execution ────────────────────────────────────────────────────

    def _execute_command(self, command: str) -> dict[str, Any]:
        builtin_result = self._run_builtin_tool(command)
        if builtin_result is not None:
            return builtin_result | {"action": command}
        try:
            output = self.env.execute(command)
        except (TimeoutError, subprocess.TimeoutExpired) as e:
            output_raw = getattr(e, "output", None)
            if isinstance(output_raw, bytes):
                output_text = output_raw.decode("utf-8", errors="replace")
            else:
                output_text = output_raw or ""
            raise ExecutionTimeoutError(self.render_template(self.config.timeout_template, output=output_text))
        self._check_submission(output)
        return output | {"action": command}

    def _process_tool_response(self, raw: dict[str, Any]) -> tuple[str, int]:
        tool_responses = LLMIDEToolResponseFormat.from_string(raw.get("output", ""))
        if not tool_responses:
            return raw.get("output", "").strip(), int(raw.get("returncode", -1))
        output_text = "\n".join(tr.output or "" for tr in tool_responses)
        last = tool_responses[-1]
        returncode = int(last.returncode if last.returncode is not None else raw.get("returncode", -1))
        for tr in tool_responses:
            self._attach_code_context_chunks(tr.code_context)
        self._apply_tool_status_updates(tool_responses)
        return output_text, returncode

    def _apply_tool_status_updates(self, tool_responses: list) -> None:
        active = self.action_manager.active_node
        for tr in tool_responses:
            pkg = getattr(tr, "package_name", None)
            if not pkg or tr.status is None:
                continue
            if active:
                active.tool_status[pkg] = tr.status
            for tool in self.installed_tools:
                if tool["name"] == pkg:
                    tool["status"] = tr.status
                    break

    def _execute_actions(self, actions: list[str]) -> tuple[list[ActionObservation], int]:
        observations: list[ActionObservation] = []
        last_returncode = 0
        for action in actions:
            raw = self._execute_command(action)
            output_text, returncode = self._process_tool_response(raw)
            observations.append(ActionObservation(
                action=action,
                observation=f"[returncode: {returncode}]\n{output_text.strip()}",
            ))
            last_returncode = returncode
            if returncode != 0:
                break
        if active := self.action_manager.active_node:
            active.code_change = self._get_git_diff()
        return observations, last_returncode

    def _attach_code_context_chunks(self, code_contexts: list[Any] | None):
        active = self.action_manager.active_node
        if not code_contexts or not active or not self.code_context_manager:
            return
        for code_context in code_contexts:
            file_path = getattr(code_context, "file_path", None)
            line_number = getattr(code_context, "line_number", None)
            if not file_path or line_number is None:
                continue
            try:
                line = int(line_number)
            except (TypeError, ValueError):
                continue
            if line < 1:
                continue
            chunk = self.code_context_manager.get_nearby_code_context(file_path, line)
            self._append_unique_code_chunk(active.code_chunks, chunk)

    @staticmethod
    def _chunk_key(chunk: CodeChunk) -> tuple:
        return (chunk.file_path, chunk.class_name, chunk.function, chunk.whole_function, tuple(chunk.lines))

    def _append_unique_code_chunk(self, chunks: list[CodeChunk], chunk: CodeChunk):
        key = self._chunk_key(chunk)
        if key not in {self._chunk_key(c) for c in chunks}:
            chunks.append(chunk)

    def _check_submission(self, output: dict[str, str]):
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() in ["MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]:
            self._save_history_text(self._get_round_index(), "response", "Submitted")
            raise Submitted("".join(lines[1:]))

    def _format_observation(self, observations: list[ActionObservation]) -> str:
        max_len = self.config.observation_max_length
        parts: list[str] = []
        for obs in observations:
            parts.append(f"[action] {obs.action}\n[observation]\n{obs.observation}")
        return "\n\n".join(parts)

    # ── Main loop ────────────────────────────────────────────────────

    def run(self, task: str, **kwargs) -> tuple[str, str]:
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        if history_dir := self._get_history_dir():
            shutil.rmtree(history_dir)
            history_dir.mkdir(parents=True, exist_ok=True)
        self._init_code_context()
        self._builtin_tools = self._init_builtin_tools()
        while True:
            try:
                self.step()
            except NonTerminatingException as e:
                logger.info(
                    f"step={self._get_round_index()} exception_type=non_terminating error={type(e).__name__}"
                )
                self.add_message("user", str(e))
            except TerminatingException as e:
                logger.info(f"step={self._get_round_index()} exception_type=terminating error={type(e).__name__}")
                self.add_message("user", str(e))
                return type(e).__name__, str(e)

    def _check_limits(self):
        if 0 < self.config.step_limit <= self.model.n_calls or 0 < self.config.cost_limit <= self.model.cost:
            raise LimitsExceeded()

    def step(self):
        self._check_limits()
        round_index = self._get_round_index()
        has_incoming_op = self.action_manager.has_pending_node
        
        system_message = self._build_system_message(has_incoming_op)
        user_message = self._build_user_message(has_incoming_op)
        self.add_message("system", system_message)
        self.add_message("user", user_message)
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

        self._save_history_text(round_index, "prompt", self._format_prompt_text(messages))

        response = self.model.query(messages)
        self.add_message("assistant", **response)
        
        content = response.get("content", "")
        if has_incoming_op:
            self._process_reflection(content, round_index)
        observations, last_returncode = self._process_action(content, round_index)
        formatted_obs = self._format_observation(observations)
        
        self.add_message("user", formatted_obs)
        self._save_history_text(
            round_index, "response",
            f"{content}\n\n{formatted_obs}",
        )
