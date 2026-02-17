#!/usr/bin/env python3

"""Run reproduction script on a single SWE-Bench instance."""

from pathlib import Path

import typer
from datasets import load_dataset

from debugmaster.environments.docker import DockerEnvironment
from debugmaster.environments.utils import setup_reproduction_script
from debugmaster.run.extra.swebench import DATASET_MAPPING, get_swebench_docker_image_name

app = typer.Typer(add_completion=False)


@app.command()
def main(
    subset: str = typer.Option("lite", "--subset", help="SWEBench subset"),
    split: str = typer.Option("dev", "--split", help="Dataset split"),
    instance_spec: str = typer.Option(..., "-i", "--instance", help="Instance ID or index"),
    source_dir: Path = typer.Option(..., "-s", "--source-dir", help="Directory with reproduction scripts"),
    target: str = typer.Option("/testbed/issue_reproduction.py", "-t", "--target", help="Target path in container"),
    timeout: int = typer.Option(60, "--timeout", help="Script execution timeout"),
    apply_patch: bool = typer.Option(False, "--apply-patch", help="Apply instance patch before running script"),
) -> None:
    """Run reproduction script on a single SWE-Bench instance."""
    instances = {
        inst["instance_id"]: inst
        for inst in load_dataset(DATASET_MAPPING.get(subset, subset), split=split)
    }
    if instance_spec.isnumeric():
        instance_spec = sorted(instances.keys())[int(instance_spec)]
    instance = instances[instance_spec]

    env = DockerEnvironment(
        image=get_swebench_docker_image_name(instance),
        cwd="/testbed",
        timeout=timeout,
        reproduction_complete=False,
        reproduction_script={"source_dir": str(source_dir), "target": target},
    )
    try:
        success, script_vars = setup_reproduction_script(env.config, env.execute, env.logger)
        if success:
            env.extra_vars.update(script_vars)

        if apply_patch:
            print("================BUG RUN================")
            print(env.extra_vars.get("script_output", "No output captured"))

            env.execute(f"cat <<'EOF' | git apply\n{instance['patch']}\nEOF")

            script_command = env.extra_vars.get("script_command", "")
            print("================FIXED RUN==========")
            if script_command:
                print(env.execute(script_command).get("output", "No output captured"))
            else:
                print("No script command available")
        else:
            print(env.extra_vars.get("script_output", "No output captured"))
    finally:
        env.cleanup()


if __name__ == "__main__":
    app()
