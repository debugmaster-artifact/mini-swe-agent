"""Environment implementations for mini-SWE-agent."""

import copy
import importlib

from debugmaster import Environment

_ENVIRONMENT_MAPPING = {
    "docker": "debugmaster.environments.docker.DockerEnvironment",
    "singularity": "debugmaster.environments.singularity.SingularityEnvironment",
    "local": "debugmaster.environments.local.LocalEnvironment",
    "swerex_docker": "debugmaster.environments.extra.swerex_docker.SwerexDockerEnvironment",
    "swerex_modal": "debugmaster.environments.extra.swerex_modal.SwerexModalEnvironment",
    "bubblewrap": "debugmaster.environments.extra.bubblewrap.BubblewrapEnvironment",
}


def get_environment_class(spec: str) -> type[Environment]:
    full_path = _ENVIRONMENT_MAPPING.get(spec, spec)
    try:
        module_name, class_name = full_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ValueError, ImportError, AttributeError):
        msg = f"Unknown environment type: {spec} (resolved to {full_path}, available: {_ENVIRONMENT_MAPPING})"
        raise ValueError(msg)


def get_environment(config: dict, *, default_type: str = "") -> Environment:
    config = copy.deepcopy(config)
    environment_class = config.pop("environment_class", default_type)
    return get_environment_class(environment_class)(**config)
