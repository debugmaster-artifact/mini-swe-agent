"""Interactive shell for a SWE-bench Docker container."""

from pathlib import Path

import typer
import yaml
from datasets import load_dataset

from debugmaster.config import builtin_config_dir, get_config_path
from debugmaster.run.extra.swebench import DATASET_MAPPING, get_sb_environment
from debugmaster.utils.log import logger

app = typer.Typer(add_completion=False)


# fmt: off
@app.command()
def main(
    subset: str = typer.Option("lite", "--subset", help="SWEBench subset to use or path to a dataset"),
    split: str = typer.Option("dev", "--split", help="Dataset split"),
    instance_spec: str = typer.Option(..., "-i", "--instance", help="Instance ID or index"),
    config_path: Path | None = typer.Option(None, "-c", "--config", help="Config file override"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment class override"),
    timeout: int = typer.Option(60, "--timeout", help="Command execution timeout in seconds"),
) -> None:
    # fmt: on
    """Drop into an interactive shell inside a SWE-bench container."""
    dataset_path = DATASET_MAPPING.get(subset, subset)
    logger.info(f"Loading dataset from {dataset_path}, split {split}...")
    instances = {
        inst["instance_id"]: inst  # type: ignore
        for inst in load_dataset(dataset_path, split=split)
    }
    if instance_spec.isnumeric():
        instance_spec = sorted(instances.keys())[int(instance_spec)]
    instance: dict = instances[instance_spec]  # type: ignore

    if config_path is None:
        config_path = builtin_config_dir / "llm-ide" / "swebench.yaml"
    resolved = get_config_path(config_path)
    logger.info(f"Loading config from '{resolved}'")
    config = yaml.safe_load(resolved.read_text())
    if environment_class is not None:
        config.setdefault("environment", {})["environment_class"] = environment_class

    env = get_sb_environment(config, instance)

    print(f"\nInstance: {instance['instance_id']}")
    print(f"Problem:  {instance['problem_statement'][:200]}...")
    print("\nType 'exit' or 'quit' to leave. Type 'help' for usage.\n")

    try:
        while True:
            try:
                cmd = input("debugmaster> ")
            except EOFError:
                break
            cmd = cmd.strip()
            if not cmd:
                continue
            if cmd in ("exit", "quit"):
                break
            if cmd == "help":
                print("  Type any shell command to execute inside the container.")
                print("  exit / quit  — leave the shell")
                print("  help         — show this message")
                continue
            out = env.execute(cmd, timeout=timeout)
            if out.get("output"):
                print(out["output"])
            print(f"[returncode {out.get('returncode', '?')}]")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        env.cleanup()


if __name__ == "__main__":
    app()
