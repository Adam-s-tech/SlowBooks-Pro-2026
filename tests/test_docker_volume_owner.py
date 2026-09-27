"""Every folder docker compose mounts a volume on is made in the image and
given to the app's user (2.18.0 gate, Linux). Docker gives an empty named
volume the owner of the image's folder; a folder the image lacked became a
volume owned by root, which the app (uid 1000) can't write: every Docker
backup failed with "Permission denied", and every upload before 2.18."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _app_volume_paths(compose: str) -> list[str]:
    """The container paths the slowbooks service mounts named volumes on."""
    named = set(re.findall(r"^  (\w+):\s*$", compose.split("\nvolumes:", 1)[1], re.M))
    service = re.search(
        r"^  slowbooks:\n(.*?)(?=^  \w+:\n|^\S|\Z)", compose, re.M | re.S
    )
    assert service, "no slowbooks service"
    mounts = re.findall(r"^\s+- (\w+):(/[^\s:]+)", service.group(1), re.M)
    return [path for name, path in mounts if name in named]


def _user_run(dockerfile: str) -> str:
    """The RUN instruction that makes the app's user, continuation lines and all."""
    m = re.search(r"^RUN useradd(?:[^\n]*\\\n)*[^\n]*\n", dockerfile, re.M)
    assert m, "no RUN useradd in the Dockerfile"
    return m.group(0)


def test_the_image_makes_and_owns_every_folder_compose_mounts_a_volume_on():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    run = _user_run(dockerfile)
    assert "mkdir -p" in run, run
    made = set(re.findall(r"(/\S+)", run.split("mkdir -p", 1)[1].split("&&", 1)[0]))
    chown = run.find("chown -R slowbooks:slowbooks /app")
    assert run.index("mkdir -p") < chown, run
    # the user takes over only after the folders are its own
    assert dockerfile.index(run) < dockerfile.index("\nUSER slowbooks")
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        paths = _app_volume_paths((ROOT / name).read_text(encoding="utf-8"))
        assert paths, name
        for path in paths:
            assert path in made and path.startswith("/app/"), (name, path, sorted(made))
