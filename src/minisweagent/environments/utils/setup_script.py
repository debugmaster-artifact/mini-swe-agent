import inspect
import logging
from pathlib import Path
from typing import Any, Callable


def get_instance_info() -> dict[str, Any]:
    """Extract instance info (subset, split, instance_spec) from the call stack."""
    keys = ('subset', 'split', 'instance_spec')
    for f in inspect.stack():
        if all(k in f.frame.f_locals for k in keys):
            return {k: f.frame.f_locals[k] for k in keys}
    return {}


def get_unique_target_path(execute_fn: Callable[[str], dict[str, Any]], target_path: str) -> str:
    """Get a unique target path by appending _{n} if the file already exists."""
    p = Path(target_path)
    n = 0
    while execute_fn(f"test -e {target_path}")['returncode'] == 0:
        n += 1
        target_path = str(p.parent / f"{p.stem}_{n}{p.suffix}")
    return target_path


def setup_reproducing_script(
    config: Any,
    execute_fn: Callable[[str], dict[str, Any]],
    logger: logging.Logger,
) -> tuple[bool, dict[str, str]]:
    """Copy reproducing script from host to container. Returns (success, script_vars)."""
    if not (cfg := getattr(config, 'reproducing_script', None)):
        return False, {}
    if not (source_dir := cfg.get('source_dir')) or not (target := cfg.get('target')):
        logger.warning("reproducing_script config missing 'source_dir' or 'target'")
        return False, {}
    info = get_instance_info()
    if not all(info.get(k) for k in ('subset', 'split', 'instance_spec')):
        logger.warning(f"Could not retrieve instance info: {info}")
        return False, {}
    host_file = Path(source_dir) / info['subset'] / info['split'] / f"{info['instance_spec']}.py"
    if not host_file.exists():
        logger.info(f"Reproducing script not found: {host_file}")
        return False, {}
    try:
        content = host_file.read_text(encoding='utf-8')
        final_path = get_unique_target_path(execute_fn, target)
        result = execute_fn(f"cat > {final_path} << 'EOF'\n{content}\nEOF")
        if result['returncode'] != 0:
            logger.error(f"Failed to write reproducing script: {result['output']}")
        exec_result = execute_fn(f"chmod +x {final_path} && python {final_path}")
        logger.info(f"Copied reproducing script to container:{final_path}")
        return True, {
            'script_location': final_path,
            'script_code': content,
            'script_command': f'python {final_path}',
            'script_output': exec_result['output'],
        }
        return False, {}
    except Exception as e:
        logger.error(f"Error setting up reproducing script: {e}")
        return False, {}
