"""Shared fail-closed policy for automatic Docker workspace bind mounts.

A mount root is a capability: callers receive it only after this module has
confirmed that the host path is a real absolute directory and not one of the
container's own workspace/home roots.  Keep this module dependency-light so the
terminal resolver and DockerEnvironment can both enforce the same boundary.
"""

import os
from typing import Optional


CONTAINER_CWD_ROOTS = ("/workspace", "/root")


def docker_volume_uses_host_path(volume_spec: object) -> bool:
    """Return whether a Docker ``-v``/``--volume`` spec names a host source.

    Docker-compatible runtimes accept POSIX, Windows, UNC, and relative bind
    sources.  This policy intentionally errs toward host access: a false
    positive merely removes an approval fast path, while a false negative can
    make an actual host bind appear isolated.
    """
    if not isinstance(volume_spec, str):
        return False
    volume = volume_spec.strip()
    if not volume:
        return False
    return (
        volume in {".", ".."}
        or volume.startswith(("/", "~", "./", "../", ".:", "..:", "\\\\"))
        or (len(volume) >= 2 and volume[0].isalpha() and volume[1] == ":")
    )


def docker_run_has_host_access(
    *,
    auto_mount_cwd: object,
    host_cwd: object,
    docker_volumes: object,
    docker_extra_args: object,
) -> bool:
    """Conservatively assess whether an effective Docker run can touch the host.

    This is the one approval-facing interpretation of Docker launch inputs.
    Automatic mounts use a previously validated task/session ``host_cwd``;
    explicit volumes are classified by source syntax.  ``docker_extra_args``
    are deliberately fail-closed: Docker forwards them verbatim, so any
    non-empty string could introduce a mount, namespace escape, device, or
    other host capability outside the modeled configuration.  Runtime options
    remain supported; they simply lose the isolated-container approval fast
    path until modeled explicitly.
    """
    if auto_mount_cwd and isinstance(host_cwd, str) and host_cwd.strip():
        return True

    if isinstance(docker_volumes, str):
        # A malformed string is forwarded item-by-item by the existing Docker
        # path; never grant the fast path for an ambiguous volume shape.
        return bool(docker_volumes.strip())
    if not isinstance(docker_volumes, (list, tuple)):
        # Downstream schema handling may reject this shape, but a nonempty value
        # is still ambiguous at the approval boundary.  Fail closed rather than
        # assuming it cannot introduce a bind in a future runtime path.
        return bool(docker_volumes)
    if any(docker_volume_uses_host_path(volume) for volume in docker_volumes):
        return True

    if isinstance(docker_extra_args, str):
        return bool(docker_extra_args.strip())
    if not isinstance(docker_extra_args, (list, tuple)):
        # Keep approval semantics aligned with DockerEnvironment's iteration of
        # decoded config.  A nonempty mapping can forward `--privileged`, a
        # bind, or another host capability as a key.
        return bool(docker_extra_args)
    return any(isinstance(arg, str) and bool(arg.strip()) for arg in docker_extra_args)


def is_in_container_cwd_root(path: object) -> bool:
    """Return whether *path* names a known in-container CWD root or descendant."""
    if not isinstance(path, str):
        return False
    return any(path == root or path.startswith(root + "/") for root in CONTAINER_CWD_ROOTS)


def validated_docker_mount_root(candidate: object) -> Optional[str]:
    """Return a host directory Docker may bind at ``/workspace``, else ``None``.

    ``host_cwd`` is a capability, not a candidate string: it is emitted only
    after the host path has been expanded, made absolute, confirmed to exist as
    a directory, and shown not to name one of the known in-container roots.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    raw_candidate = os.path.expanduser(candidate)
    if is_in_container_cwd_root(raw_candidate):
        return None
    host_root = os.path.abspath(raw_candidate)
    if not os.path.isabs(host_root) or not os.path.isdir(host_root):
        return None
    if is_in_container_cwd_root(host_root):
        return None
    return host_root
