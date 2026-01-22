import logging
import os
import shlex
import subprocess
import uuid
from typing import Any

from pydantic import BaseModel

from minisweagent.environments.utils import init_debugger_task, install_debugger, setup_reproducing_script


class DockerEnvironmentConfig(BaseModel):
    image: str
    cwd: str = "/"
    """Working directory in which to execute commands."""
    env: dict[str, str] = {}
    """Environment variables to set in the container."""
    forward_env: list[str] = []
    """Environment variables to forward to the container.
    Variables are only forwarded if they are set in the host environment.
    In case of conflict with `env`, the `env` variables take precedence.
    """
    timeout: int = 30
    """Timeout for executing commands in the container."""
    executable: str = os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker")
    """Path to the docker/container executable."""
    run_args: list[str] = ["--rm"]
    """Additional arguments to pass to the docker/container executable.
    Default is ["--rm"], which removes the container after it exits.
    """
    container_timeout: str = "2h"
    """Max duration to keep container running. Uses the same format as the sleep command."""
    pull_timeout: int = 120
    """Timeout in seconds for pulling images."""
    setup_reproducing_script: bool = False
    """Whether to set up reproducing script in the container."""
    reproducing_script: dict[str, str] = {}
    """Configuration for reproducing script setup."""
    debugger_enabled: bool = False
    """Whether to install debugger in the container."""
    debugger_package: str = ""
    """Path to the debugger package to install."""
    debugger_default_task: str | None = None
    """Default task to debug if none is specified."""


class DockerEnvironment:
    def __init__(
        self,
        *,
        config_class: type = DockerEnvironmentConfig,
        logger: logging.Logger | None = None,
        **kwargs,
    ):
        """This class executes bash commands in a Docker container using direct docker commands.
        See `DockerEnvironmentConfig` for keyword arguments.
        """
        self.logger = logger or logging.getLogger("minisweagent.environment")
        self.container_id: str | None = None
        self.config = config_class(**kwargs)
        self.extra_vars: dict[str, str] = {}
        self._start_container()

        if self.config.setup_reproducing_script:
            success, script_vars = setup_reproducing_script(self.config, self.execute, self.logger)
            if not success:
                raise RuntimeError("Failed to set up reproducing script in the container.")
            self.extra_vars.update(script_vars)

        if self.config.debugger_enabled:
            success, debugger_vars = install_debugger(self.config, self.execute, self.container_id,self.config.executable, self.logger)
            if not success:
                raise RuntimeError("Failed to install debugger in the container.")
            self.extra_vars.update(debugger_vars)
        
        if self.config.debugger_enabled and self.config.debugger_default_task:
            success, debugger_task_vars = init_debugger_task(self.execute, self.config.debugger_default_task, self.logger)
            if success:
                self.extra_vars.update(debugger_task_vars)


    def get_template_vars(self) -> dict[str, Any]:
        vars = self.config.model_dump()
        if self.extra_vars:
            vars.update(self.extra_vars)
        return vars

    def _start_container(self):
        """Start the Docker container and return the container ID."""
        container_name = f"minisweagent-{uuid.uuid4().hex[:8]}"
        cmd = [
            self.config.executable,
            "run",
            "-d",
            "--name",
            container_name,
            "-w",
            self.config.cwd,
            *self.config.run_args,
            self.config.image,
            "sleep",
            self.config.container_timeout,
        ]
        self.logger.debug(f"Starting container with command: {shlex.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,  # docker pull might take a while
            check=True,
        )
        self.logger.info(f"Started container {container_name} with ID {result.stdout.strip()}")
        self.container_id = result.stdout.strip()

    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command in the Docker container and return the result as a dict."""
        cwd = cwd or self.config.cwd
        assert self.container_id, "Container not started"

        cmd = [self.config.executable, "exec", "-w", cwd]
        for key in self.config.forward_env:
            if (value := os.getenv(key)) is not None:
                cmd.extend(["-e", f"{key}={value}"])
        for key, value in self.config.env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self.container_id, "bash", "-lc", command])

        result = subprocess.run(
            cmd,
            text=True,
            timeout=timeout or self.config.timeout,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return {"output": result.stdout, "returncode": result.returncode}

    def cleanup(self):
        """Stop and remove the Docker container."""
        if getattr(self, "container_id", None) is not None:  # if init fails early, container_id might not be set
            cmd = f"(timeout 60 {self.config.executable} stop {self.container_id} || {self.config.executable} rm -f {self.container_id}) >/dev/null 2>&1 &"
            subprocess.Popen(cmd, shell=True)

    def __del__(self):
        """Cleanup container when object is destroyed."""
        self.cleanup()
