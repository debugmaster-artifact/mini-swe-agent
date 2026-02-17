import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from jinja2 import Template

from debugmaster.config import builtin_config_dir
from debugmaster.environments.utils.llm_ide_tool_protocol import LLMIDEToolResponseFormat


@dataclass
class ExecutableConfig:
    command: str
    help: str = ""


@dataclass
class ToolConfig:
    tool_name: str
    package_name: str
    version: str | None = None
    source: str | None = None
    py_standalone: str | None = None
    installation_script: list[str] = field(default_factory=list)
    setup_script: list[str] = field(default_factory=list)
    executables: dict[str, ExecutableConfig] = field(default_factory=dict)
    description: str = ""


def load_tool_config(name: str) -> ToolConfig:
    path = builtin_config_dir / "tools" / f"{name}.yaml"
    data = yaml.safe_load(path.read_text())
    if raw_execs := data.pop("executables", None):
        data["executables"] = {k: ExecutableConfig(**v) for k, v in raw_execs.items()}
    return ToolConfig(**data)


def _run_command(execute_fn: Callable, cmd: str, description: str) -> str | None:
    """Run a command, return error message on failure or None on success."""
    result = execute_fn(cmd)
    if result["returncode"] != 0:
        return f"{description}: {result['output']}"
    return None


def _join_script_steps(steps: list[str], env_name: str | None = None) -> str:
    rendered_steps = [step.strip() for step in steps if step and step.strip()]
    if not rendered_steps:
        return ""
    joined = " && ".join(rendered_steps)
    if env_name:
        return f"LLM_IDE=1 conda run -n {env_name} bash -c {_shell_quote(joined)}"
    return f"export LLM_IDE=1 && {joined}"


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _render_script_steps(steps: list[str], template_vars: dict[str, Any]) -> list[str]:
    return [Template(step).render(template_vars).strip() for step in steps]


def _run_script_steps(
    execute_fn: Callable[[str], dict[str, Any]],
    steps: list[str],
    *,
    env_name: str | None,
    cwd: str | None = None,
    description: str,
) -> tuple[str | None, str]:
    """Run script steps. Returns (error_or_none, output)."""
    command = _join_script_steps(steps, env_name)
    if not command:
        return None, ""
    if cwd:
        command = f"cd {cwd} && {command}"
    result = execute_fn(command)
    output = result.get("output", "")
    if result["returncode"] != 0:
        return f"{description}: {output}", output
    return None, output


def _install_executable(
    execute_fn: Callable[[str], dict[str, Any]],
    *,
    container_id: str,
    docker_executable: str,
    exec_name: str,
    command: str,
    description: str,
) -> str | None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(f'#!/usr/bin/env bash\n{command} "$@"\n')
    cp_result = subprocess.run(
        [docker_executable, "cp", str(tmp_path), f"{container_id}:/usr/bin/{exec_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    tmp_path.unlink(missing_ok=True)
    if cp_result.returncode != 0:
        return f"{description}: {cp_result.stdout}{cp_result.stderr}"
    return _run_command(execute_fn, f"chmod +x /usr/bin/{exec_name}", description)


def _ensure_usr_bin_on_path(execute_fn: Callable[[str], dict[str, Any]]) -> str | None:
    path_export = 'export PATH="/usr/bin:$PATH"'
    path_update_command = (
        'case ":$PATH:" in '
        + '*":/usr/bin:"*) ;; '
        + "*) "
        + f"grep -qxF '{path_export}' ~/.bashrc || "
        + f"printf '%s\\n' '{path_export}' >> ~/.bashrc; "
        + "source ~/.bashrc ;; "
        + "esac"
    )
    return _run_command(execute_fn, path_update_command, "Failed to add /usr/bin to PATH")


def install_tools(
    tool_names: list[str],
    execute_fn: Callable[[str], dict[str, Any]],
    container_id: str,
    docker_executable: str,
    template_vars: dict[str, Any],
    tool_vars: dict[str, dict[str, str]],
    logger: logging.Logger,
) -> tuple[bool, dict[str, Any]]:
    """Install tools in the container. Returns (success, result_dict).

    On failure, result_dict contains {"error_message": "..."}.
    On success, result_dict contains {"installed_tools": [...]}.
    """
    installed = []
    for name in tool_names:
        cfg = load_tool_config(name)
        merged_vars = template_vars | tool_vars.get(name, {})

        # 1. Copy source into container
        if cfg.source and Path(cfg.source).exists():
            execute_fn("mkdir -p /tools")
            cp_result = subprocess.run(
                [docker_executable, "cp", cfg.source, f"{container_id}:/tools/{cfg.tool_name}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if cp_result.returncode != 0:
                err = f"Failed to copy source for '{cfg.tool_name}': {cp_result.stdout}{cp_result.stderr}"
                logger.error(err)
                return False, {"error_message": err}

        # 2. Create standalone conda env
        if cfg.py_standalone:
            if err := _run_command(
                execute_fn,
                f"conda create -n {cfg.tool_name} python={cfg.py_standalone} -y",
                f"Failed to create conda environment for '{cfg.tool_name}'",
            ):
                logger.error(err)
                return False, {"error_message": err}

        # 3. Installation script
        if cfg.installation_script:
            rendered_install_steps = _render_script_steps(cfg.installation_script, merged_vars)
            err, _ = _run_script_steps(
                execute_fn,
                rendered_install_steps,
                env_name=cfg.tool_name if cfg.py_standalone else None,
                cwd=f"/tools/{cfg.tool_name}" if cfg.source else None,
                description=f"Failed to install '{cfg.tool_name}'",
            )
            if err:
                logger.error(err)
                return False, {"error_message": err}

        # 4. Install executable commands
        py_env_prefix = f"conda run -n {cfg.tool_name} " if cfg.py_standalone else ""
        llm_ide_prefix = "LLM_IDE=1 "
        tool_commands = []
        for exec_name, exec_cfg in cfg.executables.items():
            rendered_exec_command = Template(exec_cfg.command).render(merged_vars).strip()
            tool_commands.append(
                {
                    "name": exec_name,
                    "command": f"{llm_ide_prefix}{py_env_prefix}{rendered_exec_command}",
                    "help": exec_cfg.help,
                    "usage": exec_cfg.help,
                }
            )

        for tool_command in tool_commands:
            if err := _install_executable(
                execute_fn,
                container_id=container_id,
                docker_executable=docker_executable,
                exec_name=tool_command["name"],
                command=tool_command["command"],
                description=f"Failed to install executable '{tool_command['name']}' for '{cfg.tool_name}'",
            ):
                logger.error(err)
                return False, {"error_message": err}

        # 5. Run setup script and get tool status
        initial_status = None
        if cfg.setup_script:
            rendered_setup_steps = _render_script_steps(cfg.setup_script, merged_vars)
            err, setup_output = _run_script_steps(
                execute_fn,
                rendered_setup_steps,
                env_name=cfg.tool_name if cfg.py_standalone else None,
                cwd=f"/tools/{cfg.tool_name}" if cfg.source else None,
                description=f"Failed to run setup script for '{cfg.tool_name}'",
            )
            if err:
                logger.error(err)
                return False, {"error_message": err}
            tool_responses = LLMIDEToolResponseFormat.from_string(setup_output)
            initial_status = next((res.status for res in tool_responses if res.status), None)

        help_lines = [f'{cfg.description.strip()}\n\n### Available commands'] if cfg.description.strip() else ['### Available commands']
        help_lines.extend(f"{index}. {tool_command['name']}: {tool_command['help']}".strip() for index, tool_command in enumerate(tool_commands, start=1))

        installed.append(
            {
                "name": cfg.tool_name,
                "help": "\n\n".join(help_lines),
                "status": initial_status,
            }
        )
        logger.info(f"Tool '{cfg.tool_name}' installed successfully")
        
    if installed:
        if err := _ensure_usr_bin_on_path(execute_fn):
            logger.error(err)
            return False, {"error_message": err}

    return True, {"installed_tools": installed}
