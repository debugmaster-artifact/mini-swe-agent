import inspect
import logging
from pathlib import Path
from typing import Any, Callable


def get_instance_info() -> dict[str, Any]:
    """Extract instance info (subset, split, instance_spec) from the call stack."""
    for f in inspect.stack():
        if 'instance' in f.frame.f_locals and f.frame.f_locals['instance']['instance_id']:
            return {'instance_spec': f.frame.f_locals['instance']['instance_id']}
    return {}


def get_unique_target_path(execute_fn: Callable[[str], dict[str, Any]], target_path: str) -> str:
    """Get a unique target path by appending _{n} if the file already exists."""
    p = Path(target_path)
    n = 0
    while execute_fn(f"test -e {target_path}")['returncode'] == 0:
        n += 1
        target_path = str(p.parent / f"{p.stem}_{n}{p.suffix}")
    return target_path


def setup_reproduction_script(
    config: Any,
    execute_fn: Callable[[str], dict[str, Any]],
    logger: logging.Logger | None = None,
) -> tuple[bool, dict[str, str]]:
    """Copy reproduction script from host to container. Returns (success, script_vars)."""
    if not (cfg := getattr(config, 'reproduction_script', None)):
        return False, {}
    if not (source_dir := cfg.get('source_dir')) or not (target := cfg.get('target')):
        if logger:
            logger.error("Reproducing script configuration is incomplete.")
        return False, {}
    info = get_instance_info()
    if not info:
        if logger:
            logger.error(f"Could not retrieve instance info: {info}")
        return False, {}
    host_file = Path(source_dir) / f"{info['instance_spec']}.py"
    if not host_file.exists():
        if logger:
            logger.error(f"Reproducing script not found: {host_file}")
        return False, {}
    try:
        content = host_file.read_text(encoding='utf-8')
        final_path = get_unique_target_path(execute_fn, target)
        result = execute_fn(f"cat > {final_path} << 'EOF'\n{content}\nEOF")
        if result['returncode'] != 0:
            if logger:
                logger.error(f"Failed to copy reproduction script to container: {result['output']}")
            return False, {}
        execute_fn(f"chmod +x {final_path} && git add . && git commit -m 'Add reproduction script'")
        exec_result = execute_fn(f"python {final_path}")
        if logger:
            logger.info(f"Reproducing script executed with return code {exec_result['returncode']}")
        return True, {
            'script_location': final_path,
            'script_code': content,
            'script_command': f'python {final_path}',
            'script_output': exec_result['output'],
        }
        return False, {}
    except Exception as e:
        if logger:
            logger.error(f"Error setting up reproduction script: {e}")
        return False, {}
