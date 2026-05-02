Mini-modules with merge-on-import — refined design
OK, simpler than my last one. Each file produces a complete MiniModule (its compilation result). When file A imports file B, A's compiler doesn't know or care that B came from a separate file — it just copies B's compiled artifacts into A's own MiniModule and uses them as if A had defined them itself.

The only difference between a local function call and an imported one is where the compiler looks for the symbol:

Local call → A's own function table.
Imported call → B's function table (which A has access to).
Once the symbol is found and pulled into A's MiniModule, it's indistinguishable from anything else A wrote.

What each file produces — one MiniModule
A MiniModule is exactly your existing Module structure. Bytecode, memory, function table, record table, entry point. Nothing exotic. A file compiles into one of these completely independently — same compile pipeline you have today, just stops short of executing.

The import mechanism — copy at first reference
When file A's compiler is processing A's body and sees call utils.format with x:

Look up format in utils's MiniModule. Found — it's a FunctionInfo with bytecode at some address in utils's code array, parameter signatures, return type.
Copy it into A's MiniModule. That means:
Append utils's format bytecode to the end of A's code array, with all its internal jumps/loads adjusted for the new offset.
Copy any constants/globals that bytecode reads, into A's initial_memory, adjusting LOAD addresses.
Recursively do the same for any other functions that format itself calls.
Register format in A's function table under the qualified name utils.format.
Emit the CALL in A's bytecode pointing at the now-local entry.
After this, utils.format is physically present in A's MiniModule. Any further calls to it (from A or from any function A imports) just resolve to that local entry. No re-copying.

The compiler doesn't have a special "this is an import" code path beyond the symbol lookup phase. Once the symbol is in A's tables, calling code is identical to calling A's own functions.

What gets copied with a function
The unit of copy is "function plus its dependencies":

Its bytecode (with addresses fixed up).
Constants it reads.
Globals it touches.
Records used in its parameters / return type / body.
Other functions it calls (transitively).
Because copying is transitive, the first reference to utils.format brings along everything format needs to actually run.

The address fixup
Each instruction in B that has an absolute address — branch target, function entry address, memory slot — needs to be rewritten when copied into A. Two ways:

Walk B's instructions during copy, look up each address against B's symbol metadata, write the equivalent address in A's space.
Or: keep B's bytecode as ASTs (the parsed function bodies) and re-emit them into A. No address arithmetic, just rerun emission.
Either works. Re-emission from AST is simpler to implement; address rewriting is faster at scale. For v1, re-emission is the right call — it reuses your existing emission code unchanged.

So a MiniModule actually carries both its bytecode AND the AST of its functions. The import-copy step re-emits the AST into the destination's bytecode space.

Idempotency
If A's main calls utils.format (which copies it in), and then A's helper also calls utils.format, the second call doesn't re-copy. A's function table already has utils.format registered → just emit a CALL to that entry.

Same applies across import paths: if A imports both utils and helpers, and both reference math.sqrt, math.sqrt ends up in A's MiniModule once, not twice. The "copy if not already present" check is by qualified name.

Loader's role
Unchanged from before. Loader walks the import graph from the entry file, produces a topo-sorted list. Compiler then processes them in order:

Compile each file into its own MiniModule, in topo order. By the time A is being compiled, B's MiniModule already exists and is ready to be referenced.
When A's compilation references utils.X, the lookup goes to utils's MiniModule (which the compiler has access to), and the copy-in happens.
The final output is the entry file's MiniModule, with everything transitively pulled in. That's the runnable program.

The mental model
Each file is its own complete program (its MiniModule).
import makes another module's symbols available for lookup.
The compiler doesn't distinguish between a local function and an imported one once the import is resolved — both end up in the same function table, addressed identically in bytecode.
Lookup path is the only difference: bare names go to local tables first; qualified names (utils.X) go to the named import's MiniModule.
When a symbol is actually used, it's copied into the current MiniModule — which is what makes this file genuinely self-contained as a compilation unit.
What to implement
Loader produces topo-sorted file list (already planned).
Compiler runs once per file. Each invocation produces a complete MiniModule (current Module struct with the AST attached).
A registry: compiled[qualifier] = MiniModule. Populated as files are compiled in topo order.
When the compiler is processing file A's body and encounters a qualified reference (e.g., call utils.format with x):
Resolve utils via A's per-file alias map → qualifier.
If utils.format is not yet in A's local function table: copy it from compiled["utils"] (re-emit from AST into A's MiniModule, transitively pulling in dependencies).
Emit the local CALL.
After all files are compiled, the entry file's MiniModule is the final program.
Each file's compilation is independent in the sense that you could compile and check it in isolation; cross-file references just become "external symbols" that get satisfied by lookup-and-copy when this file is later consumed by another.

What's deliberately not in this design
Real linker-style address relocation (we re-emit instead).
Caching MiniModules across runs.
Pre-compiled distribution.
Mutual recursion across files (still requires topo-sortable graph for now).
Those are all extensions of this same architecture — they don't require rethinking it.

TL;DR
Each file → MiniModule (bytecode + memory + function/record tables + ASTs). Loader produces topo order. Compiler processes in order, emitting one MiniModule per file. Cross-file references trigger a copy: re-emit the imported function (and its dependencies) from AST into the current MiniModule. After copy, the function is local — same lookup, same call mechanism. Final program is the entry file's MiniModule.

The compiler's only special handling for imports is at name-resolution time: where does it look up utils.format? In utils's MiniModule, then copy it in. Everything else — emission, calling convention, runtime — is identical to single-file compilation.
















# Imports — implementation plan

Goal for this round: make `use "..."` and `import "..."` work end-to-end
with cycle *detection* (reject cycles cleanly), but no clever cycle
*resolution* yet. That comes later.

## Surface

```
# Library imports — resolved from the language's own lib/ folder
use "math"
use "math" as m
use "files/binary"
from "files" use read_text, write_text

# Local imports — resolved relative to the importing file's directory
import "utils"
import "utils" as u
import "helpers/strings"
from "helpers/strings" import trim, split
```

Two keywords (`use`, `import`), same surface grammar otherwise. Imports
are allowed only at the top of a file — the first non-import statement
closes the import block, and any later imports are a parse error. This
constraint makes the header pass cheap and is what enables circular-
import handling later (when we add it).

## Pieces

1. **Lexer** — recognize `use` and `from` as keywords (`import` likely
   needs adding too).
2. **Parser** — produce import AST nodes; reject imports after any
   non-import statement.
3. **Loader** — recursively walk the import graph from the entry file,
   build the full file list with their import edges, detect cycles,
   error out if any.
4. **Compile pipeline** — once the loader has the topologically-sorted
   file list, parse and compile each file in order, merging definitions
   into one program.
5. **Symbol qualification** — `import "math"` brings `math.foo` into
   scope; `as` overrides the prefix; `from "math" import foo` brings
   unqualified.
6. **Library directory resolution** — `use "math"` looks in a fixed
   compiler-relative `lib/` folder.

## 1. Lexer changes

Add to `KEYWORDS`:
```
"use", "from", "import"
```

(`import` may already be there — check. `as` definitely exists.)

No new token kinds. Strings stay as `TK.STRING`.

## 2. Parser — the import grammar

```
import_block    := { import_stmt NEWLINE }
import_stmt     := use_form | import_form

use_form        := "use" STRING [ "as" IDENT ]
                 | "from" STRING "use" name_list

import_form     := "import" STRING [ "as" IDENT ]
                 | "from" STRING "import" name_list

name_list       := IDENT { "," IDENT }
```

After the import block ends, regular statements take over. The parser
must enforce: once a non-import statement is seen, no more imports
allowed. Mixing `use`/`import`/`from` within the block is fine — they're
all imports.

### AST node

One node type covering all four shapes:

```python
@dataclass
class ImportStmt:
    kind: str                    # "use" | "import"  (lib vs local)
    path: str                    # "math", "folder/utils", etc.
    alias: str | None            # for `import "x" as y`
    names: list[str] | None      # for `from "x" import a, b`
                                 # None when not using `from` form
```

### `parse_program` shape

```python
def parse_program(self) -> Program:
    self._skip_blank_lines()
    imports = []
    while self.peek().kind == TK.KEYWORD \
            and self.text(self.peek()) in ("use", "import", "from"):
        imports.append(self.parse_import_stmt())
        self._end_of_statement()

    stmts = []
    while self.peek().kind != TK.EOF:
        # Reject any imports that show up after the import block ended
        if self.peek().kind == TK.KEYWORD \
                and self.text(self.peek()) in ("use", "import", "from"):
            raise ParseError(
                "imports must appear at the top of the file, before any "
                "other statements",
                self.peek()
            )
        stmts.append(self.parse_statement())
        self._end_of_statement()

    return Program(imports=imports, stmts=stmts)
```

`Program` is a new top-level wrapper holding the import list separately
from the body.

## 3. Loader — building the import graph

The loader runs *before* compilation. Its job:
1. Start at the entry file.
2. Recursively load every imported file's headers (just enough to know
   its imports).
3. Detect cycles and reject.
4. Produce a topologically-sorted list of files to compile.

### Data structures

```python
@dataclass
class LoadedFile:
    abs_path: str              # canonical absolute path (cycle key)
    is_library: bool           # came from `use` (vs `import`)
    source: str                # full file contents
    tokens: list[Token]        # cached after lex
    imports: list[ImportStmt]  # parsed import block only
    full_program: Program | None  # filled in during the second pass

class Loader:
    lib_dir: str                       # compiler constant
    loaded: dict[str, LoadedFile]      # abs_path → LoadedFile, fully loaded
    in_progress: set[str]              # abs_path being walked, for cycle detection
    order: list[str]                   # topo-sorted result
```

### The walk

```python
def load(self, importer_dir: str, stmt: ImportStmt) -> str:
    """Resolve and recursively load. Returns the absolute path of the
    loaded file."""
    abs_path = self.resolve_path(importer_dir, stmt)

    if abs_path in self.loaded:
        return abs_path  # already done
    if abs_path in self.in_progress:
        raise CompileError(
            f"circular import detected: '{abs_path}' is already being "
            f"loaded. Cycle: {' -> '.join(self.in_progress)} -> {abs_path}"
        )

    self.in_progress.add(abs_path)

    source = read_file(abs_path)
    tokens = tokenize(source)
    parser = Parser(source, tokens)
    imports = parser.parse_imports_only()  # stops after import block

    file_dir = os.path.dirname(abs_path)
    for imp in imports:
        self.load(file_dir, imp)

    self.in_progress.discard(abs_path)
    self.loaded[abs_path] = LoadedFile(
        abs_path=abs_path,
        is_library=(stmt.kind == "use"),
        source=source,
        tokens=tokens,
        imports=imports,
        full_program=None,
    )
    self.order.append(abs_path)
    return abs_path
```

`order` is built post-order — a file is appended *after* all its
dependencies. So `order` is the topological sort: dependencies first,
dependents last.

### Path resolution

```python
def resolve_path(self, importer_dir: str, stmt: ImportStmt) -> str:
    if stmt.kind == "use":
        # Library — search in lib_dir
        candidate = os.path.join(self.lib_dir, stmt.path + LANG_EXTENSION)
    else:  # import
        # Local — relative to the importing file's directory
        candidate = os.path.join(importer_dir, stmt.path + LANG_EXTENSION)

    abs_path = os.path.abspath(candidate)
    if not os.path.exists(abs_path):
        raise CompileError(
            f"cannot resolve {stmt.kind} \"{stmt.path}\": "
            f"file not found at {abs_path}"
        )
    return abs_path
```

`LANG_EXTENSION` is something like `.lang` — fixed constant.

### Cycle detection summary

- `in_progress` set holds files currently being walked.
- If we encounter a file already in `in_progress`, that's a cycle —
  error out.
- If we encounter a file already in `loaded`, that's a re-import (e.g.,
  diamond dependency: A imports B and C, both import D). Skip silently
  — D was already loaded.

## 4. The "parse imports only" mode

A version of the parser that stops after the import block:

```python
def parse_imports_only(self) -> list[ImportStmt]:
    self._skip_blank_lines()
    imports = []
    while self.peek().kind == TK.KEYWORD \
            and self.text(self.peek()) in ("use", "import", "from"):
        imports.append(self.parse_import_stmt())
        self._end_of_statement()
    return imports
```

Stops at the first non-import token. Doesn't validate that the rest is
well-formed — that's the second pass's job.

This is what the loader uses during the recursive walk. It's faster
than full parsing, and it means a syntax error in some far-away file's
body doesn't block import resolution.

## 5. Compile pipeline — using the loader's output

After the loader runs, you have:
- A topologically-sorted list of absolute file paths.
- Each file's tokens cached.

The compile pipeline:
1. For each file in topo order:
   a. Full-parse the file (now you parse the bodies).
   b. Compile it into the existing `Module`, but keep track of which
      file each definition came from.
2. The entry file's executable statements run last (it's at the end of
   topo order).

### Symbol qualification

- For each file, all its top-level names (functions, records, globals)
  get a qualifier — usually the path's basename without extension
  (`math.lang` → `math`).
- For `import "x" as y` / `use "x" as y`, the qualifier is `y`.
- For `from "x" import name1, name2` / `from "x" use name1, name2`,
  those specific names are added to the importing file's scope
  unqualified.
- Other files don't see these qualified imports — qualification is
  per-file.

This means the compiler needs a per-file scope mapping during
compilation:
- `qualifier_aliases: dict[str, str]` — `math` → `math` for
  `import "math"`, or `m` → `math` for `import "math" as m`.
- `direct_imports: dict[str, str]` — `sqrt` → `math.sqrt` for
  `from "math" import sqrt`.

When compiling a `call sqrt with x`, the compiler:
1. Looks up `sqrt` in the current file's `direct_imports`. If found,
   resolves to that file's `sqrt`.
2. Otherwise looks up `sqrt` in the global functions table (current
   file's own functions).
3. Otherwise errors.

When compiling a `call math.sqrt with x`:
1. Splits `math.sqrt` into prefix (`math`) and name (`sqrt`).
2. Looks up `math` in `qualifier_aliases`. Resolves to a real file
   qualifier.
3. Looks up `<file_qualifier>.sqrt` in the global functions table.

The global functions table now has *qualified* function names like
`math.sqrt`, `utils.helper`, etc. Each file's functions are registered
under their qualifier prefix.

## 6. Library directory

A constant in the compiler:
```python
LIB_DIR = os.path.join(os.path.dirname(__file__), "lib")
```

So when run from source, `<repo>/lib/` is the lib path. Empty for now —
we'll add files later when libraries are built.

Optionally allow override via `PLAINLANG_LIB_DIR` env var:
```python
LIB_DIR = os.environ.get(
    "PLAINLANG_LIB_DIR",
    os.path.join(os.path.dirname(__file__), "lib"),
)
```

## Implementation order

### Step 1 — AST and parser changes (no behavior yet)

- Add `Program` dataclass (top-level container).
- Add `ImportStmt` dataclass.
- `parse_imports_only` method.
- Modify `parse_program` to return `Program(imports, stmts)` and reject
  imports after non-import statements.
- Lexer: add `use`, `from`, `import` to keywords if missing.

At this point the parser can produce import nodes but nothing uses them
yet. Existing tests pass because `Program.stmts` is what
`compile_program` now expects.

### Step 2 — Loader

- New file `loader.py` (or add to `compiler.py`).
- `Loader` class with state described above.
- `load_program(entry_path)` returns a list of `LoadedFile` in topo
  order.
- Cycle detection via `in_progress` set.

At this point you can call `loader.load_program("entry.lang")` and get
back a sorted list of files. Nothing compiles them yet.

### Step 3 — Pipeline integration

- Modify `run.py` to:
  1. Call the loader on the entry file path.
  2. For each loaded file in topo order, full-parse + compile, merging
     definitions into a single Module.
  3. Execute the resulting Module.

- The compile order is bottom-up: dependency files compile first, so by
  the time the entry file's body is compiled, all imported symbols are
  in the symbol table.

### Step 4 — Symbol qualification

- Each file gets a default qualifier from its filename basename.
- During its compilation, the compiler maintains the per-file
  `qualifier_aliases` and `direct_imports` maps from that file's import
  block.
- Function/record/variable lookups consult these.

### Step 5 — Tests

- Test simple `import` working.
- Test `use` resolving to lib dir.
- Test `as` aliasing.
- Test `from x import y` selective import.
- Test re-import (diamond dependency) deduping.
- Test cycle detection error message.
- Test "import after non-import statement" parse error.
- Test "library not found" error.
- Test "local file not found" error.

## Subtle decisions to make

### File extension

Pick something now and bake it into the loader. Probably `.lang`.

### Path syntax

The user writes `import "folder/utils"`, not `import "folder/utils.lang"`.
The loader appends the extension. Friendlier and standardizes the
"what's a module name" rule.

If someone has nested directories: `import "lib/math/vectors"` resolves
to `<importer_dir>/lib/math/vectors.lang`.

### Symbol qualifier when path has slashes

`import "folder/utils"` — default qualifier is `utils` (basename). Users
can `as` if they want something else.

`from "folder/utils" import x` — no qualifier needed; `x` is
unqualified.

### Top-level globals (`set`)

Top-level `set name to value` creates a global. Imported files' globals
should be accessible:
- `math.lang` has `set pi to 3.14`. After `use "math"`, the user can
  write `print math.pi`.
- `from "math" use pi` — `print pi` (unqualified).

Globals work the same as functions: the compiler registers them under
the file's qualifier and the per-file alias maps look them up.

### Re-exports

If `a.lang` does `import "b"` and uses `b.foo`, does code that imports
`a` automatically see `b.foo`? **No.** Imports are not transitive.
`c.lang` that imports `a` only sees `a`'s own definitions, not `b`'s.
To use `b`, c.lang must import b directly.

This matches Python and Java's behavior. Cleanest.

## What to leave for later

- **Circular imports actually working** (mutual recursion). For now,
  cycles are an error.
- **Per-file private/public** distinction. Everything top-level is
  exported.
- **Wildcards** (`from "x" import *`). Defer.
- **Cyclic record-layout detection**. Important but separate concern;
  only matters when records can be cyclic, which requires we have the
  cyclic-imports machinery anyway.

## Architecture summary

1. Loader walks the import graph from the entry point, building a
   topologically-sorted list of files. Detects cycles, rejects them
   with a clear error.
2. For each file in order, the compiler does a full parse + compile,
   merging definitions into a single Module. Symbol qualifiers per-file
   are tracked for resolution.
3. Existing compiler/VM are untouched — they see one big Module like
   before.

The new pieces are:
- `parse_imports_only` (small parser variant).
- `Loader` class (the graph walker).
- `Program` AST node and `ImportStmt`.
- Per-file qualifier alias maps in the compiler.

Cycles are *detected* but not *handled*; a cycle is a compile-time
error for now. Mutual recursion across files is not supported in this
phase. Adding it later means adding the two-pass header/body
compilation discussed earlier — but that's a self-contained future
change that doesn't affect the loader or the surface syntax.
