import logging
import subprocess
from pathlib import Path
from typing import Any, Callable


def install_debugger(
    config: Any,
    execute_fn: Callable[[str], dict[str, Any]],
    container_id: str,
    docker_executable: str,
    logger: logging.Logger,
) -> tuple[bool, dict[str, str]]:
    """Install debugger package in the container. Returns (success, debugger_vars)."""
    if not getattr(config, 'debugger_enabled', False):
        return False, {}
    debugger_package = getattr(config, 'debugger_package', '')
    if not debugger_package or not Path(debugger_package).exists():
        logger.warning(f"Debugger package path does not exist: {debugger_package}")
        return False, {}
    try:
        execute_fn("mkdir -p /tools")
        cp_cmd = [docker_executable, "cp", debugger_package, f"{container_id}:/tools/debugger"]
        subprocess.run(cp_cmd, check=True, capture_output=True, text=True)
        result = execute_fn("pip install -e /tools/debugger")
        if result['returncode'] != 0:
            logger.error(f"Failed to install debugger: {result['output']}")
            return False, {}
        logger.info("Debugger package installed successfully")
        return True, {}
    except Exception as e:
        logger.error(f"Error installing debugger: {e}")
        return False, {}


def init_debugger_task(
    execute_fn: Callable[[str], dict[str, Any]],
    default_task: str,
    logger: logging.Logger,
) -> tuple[bool, dict[str, str]]:
    """Start debugger server in daemon mode with the default task. Returns (success, debugger_task_vars)."""
    try:
        result = execute_fn(f"rdb server --daemon {default_task}")
        if result['returncode'] != 0:
            logger.error(f"Failed to start debugger server: {result['output']}")
            return False, {}
        logger.info(f"Debugger server started with task: {default_task}")
        result = execute_fn("rdb client connect")
        if result['returncode'] != 0:
            logger.error(f"Failed to connect to debugger server: {result['output']}")
            return False, {}
        logger.info("Connected to debugger server successfully")
        return True, {}
    except Exception as e:
        logger.error(f"Error starting debugger server: {e}")
        return False, {}
