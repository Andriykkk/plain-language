"""
Module loader.

Walks the import graph from an entry file, parses each reachable file,
detects cycles, and returns the files in **post-order** — deepest
dependencies first, entry file last. That ordering is what the multi-
file compile phase iterates: by the time a file is compiled, every
file it imports has already been compiled and its symbols are
available.

Source for each file comes through a single function — `read_source` —
that maps an absolute path to its contents. The default reads from
disk; tests pass their own callable to inject in-memory sources. The
loader doesn't distinguish between the two cases: if the function
returns a string, it's a file; if it raises, the file is missing.
"""

import os
from dataclasses import dataclass

from lexer import tokenize
from parser import Parser, Program


# All language source files use this extension. Import paths are written
# without it: `import "utils"` resolves to `utils.plang`,
# `import "helpers/strings"` resolves to `helpers/strings.plang`.
LANG_EXTENSION = ".plang"


class LoadError(Exception):
    """Raised by the loader for missing files, cycles, etc. Distinct
    from CompileError because the loader runs before any compilation
    has happened — the error categories are different (path not found
    vs. a type mismatch in code)."""
    pass


@dataclass
class LoadedFile:
    """One file's loader output. Holds the canonical absolute path, the
    raw source (for error messages and the future merge-by-AST pass),
    and the full parsed `Program` (imports + statements)."""
    abs_path: str
    source: str
    program: Program


def _disk_reader(abs_path: str) -> str:
    """Default `read_source` — open the file and return its contents.
    Raises FileNotFoundError if the path doesn't exist; the loader
    re-wraps that as a LoadError with a friendlier message."""
    with open(abs_path, "r") as f:
        return f.read()


class Loader:
    """Recursive-descent walker over the import graph.

    Source-loading is parameterized: pass `read_source=fn` where fn
    takes an absolute path and returns the file's source as a string,
    or raises if the file isn't available. The default is to read from
    disk; tests typically pass a lambda over a path-to-source dict.
    """

    def __init__(self, read_source=_disk_reader) -> None:
        self.read_source = read_source
        # Canonical absolute path → LoadedFile, for files that have been
        # fully loaded. A subsequent re-import returns the cached result
        # without re-parsing.
        self.loaded: dict[str, LoadedFile] = {}
        # Canonical absolute paths currently being walked. If a load
        # request hits one of these, we've found a cycle.
        self.in_progress: list[str] = []
        # Post-order accumulation. A file is appended after all its
        # imports have been loaded — so leaves come first, root last.
        self.order: list[str] = []

    # ----- public API -----

    def load_program(self, entry_path: str) -> list[LoadedFile]:
        """Load the entry file and everything it transitively imports.
        Returns the loaded files in compile order (post-order)."""
        abs_entry = self._canonicalize(entry_path)
        self._load_file(abs_entry)
        return [self.loaded[p] for p in self.order]

    # ----- recursion -----

    def _load_file(self, abs_path: str) -> None:
        if abs_path in self.loaded:
            return                           # diamond: already loaded
        if abs_path in self.in_progress:
            cycle = " -> ".join(self.in_progress + [abs_path])
            raise LoadError(f"circular import detected: {cycle}")

        self.in_progress.append(abs_path)
        try:
            source = self._read(abs_path)
            tokens = tokenize(source)
            program = Parser(source, tokens).parse_program()

            file_dir = os.path.dirname(abs_path)
            for imp in program.imports:
                child_path = self._resolve_path(file_dir, imp.path)
                self._load_file(child_path)

            self.loaded[abs_path] = LoadedFile(
                abs_path=abs_path, source=source, program=program,
            )
            self.order.append(abs_path)
        finally:
            self.in_progress.pop()

    # ----- path / source -----

    def _resolve_path(self, importer_dir: str, raw_path: str) -> str:
        """Canonicalize an `import "<raw_path>"` against the importing
        file's directory and append `.plang`. The actual existence
        check happens at read time — a missing file shows up as a
        LoadError from `_read`, not here."""
        candidate = os.path.join(importer_dir, raw_path + LANG_EXTENSION)
        return self._canonicalize(candidate)

    def _canonicalize(self, path: str) -> str:
        # `realpath` follows symlinks; `abspath` makes relative paths
        # absolute. Together they give one canonical name per file —
        # the dedupe / cycle-detection key.
        return os.path.realpath(os.path.abspath(path))

    def _read(self, abs_path: str) -> str:
        """Fetch a file's source via the configured reader. Any error
        (file not found, IO, missing key in a test reader) is wrapped
        in LoadError so the caller gets one consistent error type."""
        try:
            return self.read_source(abs_path)
        except Exception as e:
            raise LoadError(f"cannot load {abs_path}: {e}") from e
