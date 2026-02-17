import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from debugmaster.environments.utils import install_tools, setup_reproduction_script


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
    pull_timeout: int = 1200
    """Timeout in seconds for pulling images."""
    reproduction_complete: bool = False
    """Whether to set up reproduction script in the container."""
    reproduction_script: dict[str, str] = {}
    """Configuration for reproduction script setup."""
    tools: list[str] = []
    """Tool names to install in the container."""
    tool_vars: dict[str, dict[str, str]] = {}
    """Template variables for tool setup scripts, keyed by tool name."""


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
        self.logger = logger or logging.getLogger("debugmaster.environment")
        self.container_id: str | None = None
        self.config = config_class(**kwargs)
        self.extra_vars: dict[str, str] = {}
        self._start_container()

        if self.config.reproduction_complete:
            success, script_vars = setup_reproduction_script(self.config, self.execute, self.logger)
            if not success:
                raise RuntimeError("Failed to set up reproduction script in the container.")
            self.extra_vars.update(script_vars)

        if self.config.tools:
            success, result = install_tools(
                self.config.tools, self.execute, self.container_id, self.config.executable,
                self.config.model_dump() | self.extra_vars, self.config.tool_vars, self.logger,
            )
            if not success:
                raise RuntimeError(result["error_message"])
            self.extra_vars.update(result)


    def get_template_vars(self) -> dict[str, Any]:
        vars = self.config.model_dump()
        if self.extra_vars:
            vars.update(self.extra_vars)
        return vars

    def _start_container(self):
        """Start the Docker container and return the container ID."""
        container_name = f"debugmaster-{uuid.uuid4().hex[:8]}"
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

    def get_file(self, file_path: str) -> str:
        """Read a file from the container using docker cp."""
        assert self.container_id, "Container not started"
        logger = logging.getLogger(__name__)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            for attempt in range(3):
                try:
                    subprocess.run(
                        [self.config.executable, "cp", f"{self.container_id}:{file_path}", str(tmp_path)],
                        check=True, capture_output=True, text=True,
                    )
                    return tmp_path.read_text(encoding="utf-8", errors="replace")
                except subprocess.CalledProcessError as e:
                    logger.warning(f"docker cp failed (attempt {attempt + 1}/3): {e.stderr.strip()}")
            return ""
        finally:
            tmp_path.unlink(missing_ok=True)

    def cleanup(self):
        """Stop and remove the Docker container."""
        if getattr(self, "container_id", None) is not None:  # if init fails early, container_id might not be set
            cmd = f"(timeout 60 {self.config.executable} stop {self.container_id} || {self.config.executable} rm -f {self.container_id}) >/dev/null 2>&1 &"
            subprocess.Popen(cmd, shell=True)

    def __del__(self):
        """Cleanup container when object is destroyed."""
        self.cleanup()
