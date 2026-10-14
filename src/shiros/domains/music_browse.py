"""Read-only complete-coverage projections and transient browsing ranges.

Explicit Recording -> Work credits assert complete performances. Track credits
locate audio; they never establish a complete performance by counting tracks.
"""

from functools import cached_property
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text


class WorkCoverage:
    def __init__(self, c: Connection, scope: UUID):
        self.c, self.scope = c, scope
        self.objects = {
            r["id"]: dict(r)
            for r in c.execute(
                text(
                    "SELECT o.id,o.kind,o.title,p.data->>'work_form' AS form "
                    "FROM music_objects o LEFT JOIN music_profiles p ON p.id=o.id "
                    "AND p.scope_id=o.scope_id WHERE o.scope_id=:s"
                ),
                {"s": scope},
            ).mappings()
        }
        self.children: dict[UUID, set[UUID]] = {}
        self.parents: dict[UUID, set[UUID]] = {}
        self.recording_children: dict[UUID, set[UUID]] = {}
        self.credits: dict[UUID, set[UUID]] = {}
        self.audio: dict[UUID, set[UUID]] = {}
        self._leaves: dict[UUID, set[UUID]] = {}
        self._coverage: dict[UUID, set[UUID]] = {}
        for r in c.execute(
            text(
                "SELECT subject_id,object_id,role FROM music_relations "
                "WHERE scope_id=:s AND role IN "
                "('parent_work','parent_recording','work','recording')"
            ),
            {"s": scope},
        ).mappings():
            a, b, role = r["subject_id"], r["object_id"], r["role"]
            if a not in self.objects or b not in self.objects:
                continue
            if role == "parent_work":
                self.children.setdefault(b, set()).add(a)
                self.parents.setdefault(a, set()).add(b)
            elif role == "parent_recording":
                self.recording_children.setdefault(b, set()).add(a)
            elif role == "work":
                self.credits.setdefault(a, set()).add(b)
            elif role == "recording":
                self.audio.setdefault(a, set()).add(b)

    def require(self, identifier: UUID, kind: str) -> None:
        if self.objects.get(identifier, {}).get("kind") != kind:
            raise ValueError("music.invalid_relation")

    def leaves(self, work: UUID, path: frozenset[UUID] = frozenset()) -> set[UUID]:
        if work in path:
            raise ValueError("music.work_cycle")
        if work not in self._leaves:
            children = self.children.get(work, set())
            if children:
                self._leaves[work] = set().union(
                    *(self.leaves(child, path | {work}) for child in children)
                )
            else:
                self._leaves[work] = set() if self.objects[work]["form"] == "folder" else {work}
        return self._leaves[work]

    def family(self, recording: UUID) -> set[UUID]:
        self.require(recording, "recording")
        result, pending = set(), [recording]
        while pending:
            node = pending.pop()
            if node not in result:
                result.add(node)
                pending.extend(self.recording_children.get(node, set()))
        return result

    def recorded_leaves(self, recording: UUID) -> set[UUID]:
        if recording not in self._coverage:
            explicit = set().union(*(self.credits.get(r, set()) for r in self.family(recording)))
            self._coverage[recording] = set().union(*(self.leaves(w) for w in explicit))
        return self._coverage[recording]

    @cached_property
    def work_leaves(self) -> dict[UUID, set[UUID]]:
        return {i: self.leaves(i) for i, o in self.objects.items() if o["kind"] == "work"}

    def complete_works(self, recording: UUID) -> set[UUID]:
        leaves = self.recorded_leaves(recording)
        direct = set().union(*(self.credits.get(r, set()) for r in self.family(recording)))
        return direct | {
            w for w, members in self.work_leaves.items() if members and members <= leaves
        }

    def complete_recordings(self, work: UUID) -> list[UUID]:
        self.require(work, "work")
        members = self.leaves(work)
        return [
            r
            for r, o in self.objects.items()
            if o["kind"] == "recording"
            and (
                work in self.credits.get(r, set())
                or (members and members <= self.recorded_leaves(r))
            )
        ]

    def album_parents(self, work: UUID) -> set[UUID]:
        result, seen, pending = set(), set(), list(self.parents.get(work, set()))
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            if self.objects[node]["form"] == "album":
                result.add(node)
            else:
                pending.extend(self.parents.get(node, set()))
        return result

    def display_works(self, recording: UUID) -> list[UUID]:
        covered = self.complete_works(recording)
        albums = set().union(*(self.album_parents(w) for w in covered))
        # A partial album is a navigation destination, not a complete-coverage fact.
        return sorted(albums | {w for w in covered if not self.album_parents(w)}, key=str)

    def child_range(self, work: UUID, recording: UUID) -> list[UUID]:
        self.require(work, "work")
        covered = self.recorded_leaves(recording)
        return [child for child in self.children.get(work, set()) if self.leaves(child) & covered]

    def release_range(
        self, release: UUID, work: UUID | None, recording: UUID | None
    ) -> dict[str, Any]:
        self.require(release, "release")
        if work is None and recording is None:
            raise ValueError("music.invalid_filter")
        allowed = set().union(*self.work_leaves.values())
        if work:
            self.require(work, "work")
            allowed &= self.leaves(work)
        family = None
        if recording:
            family = self.family(recording)
            allowed &= self.recorded_leaves(recording)
        tracks: list[UUID] = list(
            self.c.execute(
                text(
                    "SELECT id FROM music_tracks WHERE scope_id=:s "
                    "AND release_id=:id ORDER BY disc,position"
                ),
                {"s": self.scope, "id": release},
            ).scalars()
        )
        matched, unknown = [], 0
        for track in tracks:
            audio = self.audio.get(track, set())
            if family is not None and not audio:
                release_audio = self.audio.get(release, set())
                if len(release_audio) == 1 and any(self.family(r) & family for r in release_audio):
                    audio = release_audio
                else:
                    unknown += 1
                    continue
            if family is not None and audio and not any(self.family(r) & family for r in audio):
                continue
            direct = self.credits.get(track, set())
            located = set().union(*(self.leaves(w) for w in direct))
            if not direct:
                candidates = set().union(*(self.recorded_leaves(r) for r in audio))
                # Multi-work recordings cannot locate individual tracks without credits.
                if len(candidates) == 1 or candidates and candidates <= allowed:
                    located = candidates
            # A broad group credit is ambiguous for a narrower requested range too.
            if located and located <= allowed:
                matched.append(track)
            elif not located or located & allowed:
                unknown += 1
        return {
            "track_ids": matched,
            "total_tracks": len(tracks),
            "unknown_tracks": unknown,
            "matched_tracks": len(matched),
        }
