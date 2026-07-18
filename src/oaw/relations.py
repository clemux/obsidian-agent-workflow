"""Semantic task relationships, graph validation, and derived blocker state."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import (
    append_frontmatter_list_value,
    parse_frontmatter,
    parse_yaml_string_list_item,
    remove_frontmatter_list_value,
)
from .links import durable_link_target, durable_wikilink, normalize_link_target, parse_wikilinks
from .resolver import (
    NoteMatch,
    NoteReference,
    resolve_id_from_references,
    scan_note_references,
)

RELATION_TYPES = ("blocked-by", "follows", "follow-up-to")


@dataclass(frozen=True)
class RelationIssue:
    code: str
    source: NoteMatch
    relation_type: str
    message: str
    target: NoteMatch | None = None
    involved_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "source": self.source.note_id,
            "source_path": self.source.relpath,
            "type": self.relation_type,
            "target": self.target.note_id if self.target else None,
            "target_path": self.target.relpath if self.target else None,
            "message": self.message,
        }


@dataclass(frozen=True)
class RelationEdge:
    source: NoteMatch
    relation_type: str
    raw_value: str
    target: NoteMatch | None
    issues: tuple[str, ...] = ()
    cyclic: bool = False

    @property
    def state(self) -> str:
        if self.issues or self.cyclic or self.target is None:
            return "invalid"
        if self.relation_type != "blocked-by":
            return "informational"
        return "satisfied" if self.target.frontmatter.get("status") == "done" else "blocked"

    def as_dict(self, direction: str) -> dict[str, object]:
        return {
            "direction": direction,
            "type": self.relation_type,
            "source": self.source.note_id,
            "source_path": self.source.relpath,
            "source_status": self.source.frontmatter.get("status", ""),
            "target": self.target.note_id if self.target else None,
            "target_path": self.target.relpath if self.target else None,
            "target_status": self.target.frontmatter.get("status", "") if self.target else None,
            "state": self.state,
            "link": self.raw_value,
            "issues": list(self.issues)
            + (["relationship participates in a cycle"] if self.cyclic else []),
        }


@dataclass(frozen=True)
class RelationGraph:
    references: tuple[NoteReference, ...]
    tasks_by_path: dict[str, NoteMatch]
    all_metadata_by_path: dict[str, dict[str, object]]
    edges: tuple[RelationEdge, ...]
    issues: tuple[RelationIssue, ...]


@dataclass(frozen=True)
class RelationMutation:
    source: NoteMatch
    target: NoteMatch
    relation_type: str
    link: str
    updated_text: str
    changed: bool


@dataclass(frozen=True)
class BlockerProblem:
    state: str
    message: str


def is_relation_task(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return (
        (len(parts) == 4 and parts[0] == "Projects" and parts[2] == "Tasks")
        or (len(parts) == 3 and parts[:2] == ("Agents", "Tasks"))
        or (len(parts) == 2 and parts[0] == "Tasks")
    )


def _path_key(relpath: str) -> str:
    return Path(relpath).with_suffix("").as_posix()


def _match_from_reference(
    reference: NoteReference, data: dict[str, object], root: Path
) -> NoteMatch:
    note_id = data.get("id")
    return NoteMatch(
        path=reference.path,
        relpath=reference.relpath,
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by="scan",
        title=reference.path.stem,
        frontmatter_text=reference.frontmatter_text,
        frontmatter=data,
    )


def _relation_values(match: NoteMatch, relation_type: str) -> list[str]:
    lines = match.frontmatter_text.splitlines()
    pattern = re.compile(rf"^{re.escape(relation_type)}\s*:\s*(.*)$")
    fields = [
        (index, found.group(1))
        for index, line in enumerate(lines)
        if (found := pattern.match(line))
    ]
    if len(fields) > 1:
        raise OawError(f"task relation frontmatter contains duplicate field: {relation_type}")
    if not fields:
        return []
    index, inline = fields[0]
    if inline.strip():
        raise OawError(f"{relation_type} must use a YAML block list")

    values: list[str] = []
    for line in lines[index + 1 :]:
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("\t"):
            raise OawError(f"{relation_type} indentation must use spaces")
        item = re.fullmatch(r"\s+-\s+(.+)", line)
        if item is None:
            raise OawError(f"{relation_type} must be a flat YAML block list")
        values.append(parse_yaml_string_list_item(item.group(1), relation_type))
    return values


def _link_from_value(raw_value: str, relation_type: str):
    links = parse_wikilinks(raw_value)
    if len(links) != 1 or links[0].raw != raw_value or raw_value.startswith("!"):
        raise OawError(f"{relation_type} items must each be one internal wikilink")
    link = links[0]
    if "#" in link.target or "^" in link.target:
        raise OawError(f"{relation_type} links must target whole task notes")
    return link


def _task_from_graph(graph: RelationGraph, raw_id: str, root: Path) -> NoteMatch:
    match = resolve_id_from_references(raw_id, root, graph.references)
    if match.frontmatter.get("type") != "task" or not is_relation_task(match.path, root):
        raise OawError(
            "task relations require task notes under Projects/*/Tasks, Agents/Tasks, or root Tasks"
        )
    if not match.note_id:
        raise OawError("task relations require a stable frontmatter id")
    return match


def _cycle_issues_for_type(relation_type: str, edges: list[RelationEdge]) -> list[RelationIssue]:
    issues: list[RelationIssue] = []
    adjacency: dict[str, list[RelationEdge]] = defaultdict(list)
    matches: dict[str, NoteMatch] = {}
    for edge in edges:
        if edge.relation_type != relation_type or edge.target is None:
            continue
        source_key = _path_key(edge.source.relpath)
        target_key = _path_key(edge.target.relpath)
        adjacency[source_key].append(edge)
        matches[source_key] = edge.source
        matches[target_key] = edge.target

    visited: set[str] = set()
    stack: list[str] = []
    positions: dict[str, int] = {}
    seen_cycles: set[frozenset[str]] = set()

    def visit(node: str) -> None:
        visited.add(node)
        positions[node] = len(stack)
        stack.append(node)
        for edge in adjacency.get(node, []):
            assert edge.target is not None
            target = _path_key(edge.target.relpath)
            if target in positions:
                cycle_nodes = stack[positions[target] :] + [target]
                cycle_key = frozenset(cycle_nodes[:-1])
                if cycle_key not in seen_cycles:
                    seen_cycles.add(cycle_key)
                    labels = [
                        matches[item].note_id or matches[item].relpath for item in cycle_nodes
                    ]
                    issues.append(
                        RelationIssue(
                            code="cycle",
                            source=edge.source,
                            relation_type=relation_type,
                            target=edge.target,
                            message=f"{relation_type} cycle: {' -> '.join(labels)}",
                            involved_paths=tuple(cycle_nodes[:-1]),
                        )
                    )
            elif target not in visited:
                visit(target)
        stack.pop()
        positions.pop(node, None)

    for node in sorted(matches):
        if node not in visited:
            visit(node)
    return issues


def _cycle_issues(edges: list[RelationEdge]) -> list[RelationIssue]:
    return [
        issue
        for relation_type in RELATION_TYPES
        for issue in _cycle_issues_for_type(relation_type, edges)
    ]


def build_relation_graph(root: Path) -> RelationGraph:
    references = tuple(scan_note_references(root))
    tasks_by_path: dict[str, NoteMatch] = {}
    all_metadata_by_path: dict[str, dict[str, object]] = {}
    for reference in references:
        data = parse_frontmatter(reference.frontmatter_text)
        key = _path_key(reference.relpath)
        all_metadata_by_path[key] = data
        if data.get("type") == "task" and is_relation_task(reference.path, root):
            tasks_by_path[key] = _match_from_reference(reference, data, root)

    edges: list[RelationEdge] = []
    issues: list[RelationIssue] = []
    for source in sorted(tasks_by_path.values(), key=lambda item: item.relpath):
        for relation_type in RELATION_TYPES:
            try:
                values = _relation_values(source, relation_type)
            except OawError as exc:
                issues.append(RelationIssue("shape", source, relation_type, str(exc)))
                continue
            seen_targets: set[str] = set()
            for raw_value in values:
                edge_issues: list[str] = []
                target: NoteMatch | None = None
                try:
                    link = _link_from_value(raw_value, relation_type)
                except OawError as exc:
                    edge_issues.append(str(exc))
                    link = None
                target_key = normalize_link_target(link.target) if link is not None else ""
                if link is not None:
                    if target_key in seen_targets:
                        edge_issues.append("duplicate relationship target")
                    seen_targets.add(target_key)
                    target = tasks_by_path.get(target_key)
                    if target is None:
                        if target_key in all_metadata_by_path:
                            edge_issues.append("relationship target is not a supported task note")
                        else:
                            edge_issues.append("relationship target does not resolve")
                    else:
                        if not target.note_id:
                            edge_issues.append(
                                "relationship target must have a stable frontmatter id"
                            )
                        canonical_target = durable_link_target(target)
                        if link.target != canonical_target or link.alias != target.note_id:
                            edge_issues.append(
                                "relationship must use the canonical durable path with the target ID as its label"
                            )
                        if target.path == source.path:
                            edge_issues.append("task cannot relate to itself")
                edge = RelationEdge(source, relation_type, raw_value, target, tuple(edge_issues))
                edges.append(edge)
                for message in edge_issues:
                    issues.append(RelationIssue("edge", source, relation_type, message, target))
    cycle_issues = _cycle_issues(edges)
    issues.extend(cycle_issues)
    cycle_members = [(issue.relation_type, set(issue.involved_paths)) for issue in cycle_issues]
    edges = [
        RelationEdge(
            edge.source,
            edge.relation_type,
            edge.raw_value,
            edge.target,
            edge.issues,
            any(
                edge.relation_type == relation_type
                and _path_key(edge.source.relpath) in members
                and edge.target is not None
                and _path_key(edge.target.relpath) in members
                for relation_type, members in cycle_members
            ),
        )
        for edge in edges
    ]
    return RelationGraph(
        references,
        tasks_by_path,
        all_metadata_by_path,
        tuple(edges),
        tuple(issues),
    )


def _reachable_paths(graph: RelationGraph, start: NoteMatch) -> set[str]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.target is not None:
            adjacency[_path_key(edge.source.relpath)].add(_path_key(edge.target.relpath))
    reachable: set[str] = set()
    queue = deque([_path_key(start.relpath)])
    while queue:
        node = queue.popleft()
        if node in reachable:
            continue
        reachable.add(node)
        queue.extend(adjacency.get(node, ()))
    return reachable


def _selected_issues(graph: RelationGraph, task: NoteMatch | None) -> list[RelationIssue]:
    if task is None:
        return list(graph.issues)
    reachable = _reachable_paths(graph, task)
    return [
        issue
        for issue in graph.issues
        if _path_key(issue.source.relpath) in reachable
        or bool(reachable.intersection(issue.involved_paths))
    ]


def validate_task_relations(root: Path, task_value: str | None, json_output: bool) -> None:
    graph = build_relation_graph(root)
    task = _task_from_graph(graph, task_value, root) if task_value else None
    issues = _selected_issues(graph, task)
    if json_output:
        print(json.dumps([issue.as_dict() for issue in issues], indent=2, ensure_ascii=False))
    elif issues:
        for issue in issues:
            source = issue.source.note_id or issue.source.relpath
            print(f"{source}: {issue.relation_type}: {issue.message}")
    else:
        suffix = f" ({task.note_id})" if task else ""
        print(f"Task relation validation: clean{suffix}")
    if issues:
        raise OawError(f"task relation validation found {len(issues)} issue(s)")


def list_task_relations(root: Path, task_value: str, incoming: bool, json_output: bool) -> None:
    graph = build_relation_graph(root)
    task = _task_from_graph(graph, task_value, root)
    if incoming:
        edges = [edge for edge in graph.edges if edge.target and edge.target.path == task.path]
        direction = "incoming"
    else:
        edges = [edge for edge in graph.edges if edge.source.path == task.path]
        direction = "outgoing"
    selected_issues = [
        issue
        for issue in graph.issues
        if issue.source.path == task.path
        or task.relpath.removesuffix(".md") in issue.involved_paths
        or (incoming and issue.target is not None and issue.target.path == task.path)
    ]
    if json_output:
        print(
            json.dumps(
                {
                    "task": task.note_id,
                    "direction": direction,
                    "relations": [edge.as_dict(direction) for edge in edges],
                    "issues": [issue.as_dict() for issue in selected_issues],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(f"Task: {task.note_id}")
    print(f"Direction: {direction}")
    for edge in edges:
        source = edge.source.note_id or edge.source.relpath
        target = edge.target.note_id if edge.target else "(invalid target)"
        status = edge.target.frontmatter.get("status", "") if edge.target else "invalid"
        if incoming:
            print(f"- {source} --{edge.relation_type}--> {target} | state: {edge.state}")
        else:
            print(f"- {edge.relation_type} -> {target} | status: {status} | state: {edge.state}")
    if not edges:
        print(f"No {direction} semantic relations.")
    if selected_issues:
        print("Issues:")
        for issue in selected_issues:
            print(f"- {issue.relation_type}: {issue.message}")


def blocker_problems(root: Path, match: NoteMatch) -> list[BlockerProblem]:
    graph = build_relation_graph(root)
    source_key = _path_key(match.relpath)
    problems: list[BlockerProblem] = []
    seen: set[tuple[str, str]] = set()
    for issue in graph.issues:
        if issue.relation_type != "blocked-by":
            continue
        if _path_key(issue.source.relpath) != source_key and source_key not in issue.involved_paths:
            continue
        item = ("invalid", issue.message)
        if item not in seen:
            seen.add(item)
            problems.append(BlockerProblem(*item))
    for edge in graph.edges:
        if edge.relation_type != "blocked-by" or _path_key(edge.source.relpath) != source_key:
            continue
        if edge.issues or edge.cyclic or edge.target is None:
            continue
        status = str(edge.target.frontmatter.get("status", ""))
        if status == "done":
            continue
        target = edge.target.note_id or edge.target.relpath
        item = ("blocked", f"{target} (status: {status or 'missing'})")
        if item not in seen:
            seen.add(item)
            problems.append(BlockerProblem(*item))
    return problems


def _path_reaches(graph: RelationGraph, relation_type: str, start: str, wanted: str) -> bool:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.relation_type == relation_type and edge.target is not None:
            adjacency[_path_key(edge.source.relpath)].add(_path_key(edge.target.relpath))
    queue = deque([start])
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        if node == wanted:
            return True
        if node in visited:
            continue
        visited.add(node)
        queue.extend(adjacency.get(node, ()))
    return False


def prepare_relation_add(
    root: Path, source_value: str, relation_type: str, target_value: str
) -> RelationMutation:
    if relation_type not in RELATION_TYPES:
        raise OawError(f"unsupported task relation type: {relation_type}")
    graph = build_relation_graph(root)
    source = _task_from_graph(graph, source_value, root)
    target = _task_from_graph(graph, target_value, root)
    if source.path == target.path:
        raise OawError("task cannot relate to itself")
    values = _relation_values(source, relation_type)
    canonical = durable_wikilink(target, target.note_id)
    target_key = _path_key(target.relpath)
    existing_canonical = False
    for value in values:
        link = _link_from_value(value, relation_type)
        if normalize_link_target(link.target) != target_key:
            continue
        if value == canonical:
            existing_canonical = True
        else:
            raise OawError(
                f"existing {relation_type} relationship to {target.note_id} is not canonical; "
                "remove it before adding the canonical relationship"
            )
    source_issues = [
        issue.message
        for issue in graph.issues
        if issue.source.path == source.path and issue.relation_type == relation_type
    ]
    if source_issues:
        raise OawError(
            f"cannot add {relation_type} while existing relationships are invalid: "
            + "; ".join(dict.fromkeys(source_issues))
        )
    if existing_canonical:
        return RelationMutation(
            source,
            target,
            relation_type,
            canonical,
            source.path.read_text(encoding="utf-8"),
            False,
        )
    if _path_reaches(graph, relation_type, _path_key(target.relpath), _path_key(source.relpath)):
        raise OawError(
            f"adding {source.note_id} {relation_type} {target.note_id} would create a cycle"
        )
    text = append_frontmatter_list_value(
        source.path.read_text(encoding="utf-8"), relation_type, canonical
    )
    return RelationMutation(source, target, relation_type, canonical, text, True)


def prepare_relation_remove(
    root: Path, source_value: str, relation_type: str, target_value: str
) -> RelationMutation:
    if relation_type not in RELATION_TYPES:
        raise OawError(f"unsupported task relation type: {relation_type}")
    graph = build_relation_graph(root)
    source = _task_from_graph(graph, source_value, root)
    target = _task_from_graph(graph, target_value, root)
    values = _relation_values(source, relation_type)
    target_key = _path_key(target.relpath)
    matches: list[str] = []
    for value in values:
        link = _link_from_value(value, relation_type)
        if normalize_link_target(link.target) == target_key:
            matches.append(value)
    if not matches:
        raise OawError(f"{source.note_id} has no {relation_type} relationship to {target.note_id}")
    text = source.path.read_text(encoding="utf-8")
    for value in dict.fromkeys(matches):
        text = remove_frontmatter_list_value(text, relation_type, value)
    return RelationMutation(
        source,
        target,
        relation_type,
        durable_wikilink(target, target.note_id),
        text,
        True,
    )
