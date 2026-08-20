"""The three things that together mean "a project is open".

A ProjectState, the Manifest it was built from, and the AliceTools that
can act on both are useless apart. Every window operation needs all three,
and they are only ever replaced together, by opening a project or by
re-extracting one.

Held separately they were three independently nullable fields, which meant
every caller invented its own idea of what "open" meant, and a failure
partway through opening left the new project paired with the previous
one's manifest. Bundling them makes a half-open window unrepresentable.
Frozen, so refreshing one part is an explicit swap of the whole via
dataclasses.replace rather than an in-place write nobody else can see.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .alice_tools import AliceTools
from .manifest import Manifest, parse_manifest
from .project import ProjectState


@dataclass(frozen=True)
class OpenProject:
    project: ProjectState
    manifest: Manifest
    tools: AliceTools

    @classmethod
    def load(cls, project_file: str | Path) -> OpenProject:
        """Read a saved project and everything it points at.

        Raises rather than returning a partial result, so a caller that
        assigns the return value can only ever assign a complete one. The
        manifest is the usual failure, since a game update or a moved
        working folder can leave a project file pointing at one that is
        gone or unreadable.
        """
        project = ProjectState.load(project_file)
        manifest = parse_manifest(project.manifest_path)
        return cls(project=project, manifest=manifest, tools=AliceTools(project.alice_exe_path))

    def reloaded_manifest(self) -> OpenProject:
        """The same open project, with its manifest re-read from disk.

        Used after a re-extract, which rewrites the manifest underneath a
        project that is otherwise unchanged.
        """
        return replace(self, manifest=parse_manifest(self.project.manifest_path))
