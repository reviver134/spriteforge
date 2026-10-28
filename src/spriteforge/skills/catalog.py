"""Workspace skill installation and role-scoped progressive disclosure."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from spriteforge.core.security import redact
from spriteforge.memory.okf.git import write_lock


class SkillHeader(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    description: str = Field(min_length=1, max_length=4096)


class Skill(BaseModel):
    header: SkillHeader
    directory: Path
    body: str


class Grants(BaseModel):
    roles: dict[str, list[str]] = Field(default_factory=dict)


def read_skill(directory: Path) -> Skill:
    if directory.is_symlink():
        raise ValueError("Skill directories cannot be symlinks")
    directory = directory.resolve()
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError("Skill resources must stay inside the skill folder")
    text = (directory / "SKILL.md").read_text(encoding="utf-8-sig")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    _, front, body = text.split("---", 2)
    header = SkillHeader.model_validate(yaml.safe_load(front))
    if not body.strip():
        raise ValueError("Skill body must not be empty")
    if redact(text) != text:
        raise ValueError("Skill contains a credential from the environment")
    return Skill(header=header, directory=directory, body=body.strip())


class SkillCatalog:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = self.workspace / "skills"
        self.root.mkdir(parents=True, exist_ok=True)
        self.grants_path = self.workspace / "skill-grants.json"

    def all(self):
        result = {}
        for root in (Path(__file__).with_name("builtin"), self.root):
            for path in sorted(root.glob("*/SKILL.md")):
                skill = read_skill(path.parent)
                if skill.header.name in result:
                    raise ValueError(f"Duplicate skill: {skill.header.name}")
                result[skill.header.name] = skill
        return result

    def grants(self):
        return (
            Grants.model_validate_json(self.grants_path.read_text())
            if self.grants_path.exists()
            else Grants()
        )

    def available(self, role):
        allowed = set(role.allowed_skills) | set(self.grants().roles.get(role.name, []))
        return {n: s for n, s in self.all().items() if n in allowed}

    def summaries(self, role):
        return [
            {"name": n, "description": s.header.description}
            for n, s in self.available(role).items()
        ]

    def load(self, role, name, resource="SKILL.md"):
        skills = self.available(role)
        if name not in skills:
            raise PermissionError(f"Skill not allowed: {name}")
        skill = skills[name]
        if resource == "SKILL.md":
            return skill.body
        path = (skill.directory / resource).resolve()
        if not path.is_relative_to(skill.directory) or Path(resource).parts[0] not in {
            "references",
            "scripts",
            "assets",
        }:
            raise ValueError("Only skill references and scripts may be read")
        return path.read_text(encoding="utf-8")  # Never execute bundled scripts.

    def add(self, source: str, role="Pixel Artist", subdir="", revision: str | None = None):
        from spriteforge.agents.roles import load_roles

        if role not in load_roles():
            raise ValueError("Unknown role")
        with tempfile.TemporaryDirectory(dir=self.workspace) as temp:
            if source.startswith("https://"):
                from urllib.parse import urlsplit

                url = urlsplit(source)
                if url.username or url.password or url.query or url.fragment:
                    raise ValueError("Use a credential-free Git URL")
                checkout = Path(temp) / "checkout"
                result = subprocess.run(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "clone",
                        "--depth",
                        "1",
                        "--",
                        source,
                        str(checkout),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode:
                    raise ValueError(redact(result.stderr))
                if revision:
                    for args in (
                        ["fetch", "--depth", "1", "origin", revision],
                        ["checkout", "--detach", "FETCH_HEAD"],
                    ):
                        result = subprocess.run(
                            ["git", "-C", str(checkout), *args],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode:
                            raise ValueError(redact(result.stderr))
            else:
                checkout = Path(source).resolve()
            directory = (checkout / subdir).resolve()
            if not directory.is_relative_to(checkout.resolve()):
                raise ValueError("Skill subdirectory escapes source")
            skill = read_skill(directory)
            with write_lock(self.workspace / ".skills.lock"):
                if skill.header.name in self.all():
                    raise ValueError(
                        "Skill already installed; remove it explicitly before replacing"
                    )
                target = self.root / skill.header.name
                shutil.copytree(
                    directory, target, ignore=shutil.ignore_patterns(".git", "__pycache__")
                )
                grants = self.grants()
                grants.roles.setdefault(role, []).append(skill.header.name)
                self.grants_path.write_text(grants.model_dump_json(indent=2), encoding="utf-8")
            return skill.header.name

    def remove(self, name):
        SkillHeader(name=name, description="remove")
        target = (self.root / name).resolve()
        if target.parent != self.root.resolve() or not target.is_dir():
            raise ValueError("Only installed workspace skills can be removed")
        with write_lock(self.workspace / ".skills.lock"):
            shutil.rmtree(target)
            grants = self.grants()
            grants.roles = {r: [n for n in names if n != name] for r, names in grants.roles.items()}
            self.grants_path.write_text(grants.model_dump_json(indent=2), encoding="utf-8")
