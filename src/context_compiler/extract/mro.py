"""C3 class linearization and flattened member surfaces."""

from __future__ import annotations


class MROError(ValueError):
    pass


def _merge(sequences: list[list[str]]) -> list[str]:
    out: list[str] = []
    while any(sequences):
        sequences = [seq for seq in sequences if seq]
        candidate = next((seq[0] for seq in sequences
                          if not any(seq[0] in other[1:] for other in sequences)), None)
        if candidate is None:
            raise MROError("inconsistent hierarchy")
        out.append(candidate)
        for seq in sequences:
            if seq and seq[0] == candidate:
                seq.pop(0)
    return out


def c3_linearize(cls: str, bases: dict[str, list[str]]) -> list[str]:
    active: set[str] = set()
    cache: dict[str, list[str]] = {}

    def linearize(name: str) -> list[str]:
        if name in cache:
            return cache[name]
        if name in active:
            raise MROError("inheritance cycle")
        if name not in bases:
            raise MROError(f"unresolvable base: {name}")
        active.add(name)
        direct = bases[name]
        result = [name] + _merge([linearize(base).copy() for base in direct] + [direct.copy()])
        active.remove(name)
        cache[name] = result
        return result

    return linearize(cls)


def flatten_members(cls: str, bases: dict[str, list[str]], members: dict[str, list[str]]) -> tuple[list[tuple[str, str]], bool]:
    try:
        order = c3_linearize(cls, bases)
    except MROError:
        return [(member, cls) for member in members.get(cls, [])], True
    seen: set[str] = set()
    flattened: list[tuple[str, str]] = []
    for owner in order:
        for member in members.get(owner, []):
            name = member.split("(", 1)[0].split(":", 1)[0].strip().split()[-1]
            if name not in seen:
                flattened.append((member, owner))
                seen.add(name)
    return flattened, False
