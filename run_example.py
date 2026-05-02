"""Run a multi-file `.plang` program from disk.

Wires the loader (recursive import walker) to the existing compiler.
The loader produces a topologically-sorted list of files (deepest
dependencies first); their statement bodies are concatenated in that
order and fed to the existing single-shot `compile_program`. Functions
from imported files end up registered before main's body compiles, so
calls across files resolve through the same function-registry path
that single-file calls already use.

Usage:
    python3 run_example.py example/main.plang
    python3 run_example.py                          # defaults to example/main.plang
"""

import os
import sys

# Make the project root importable regardless of where this script is run from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loader import Loader
from compiler import compile_program
from vm import execute


def run_file(entry_path: str) -> None:
    files = Loader().load_program(entry_path)
    # Concatenate statement bodies in compile order. The loader already
    # consumed the import statements; what's left in each file is its
    # actual definitions and executable code.
    merged_stmts = []
    for f in files:
        merged_stmts.extend(f.program.stmts)

    module = compile_program(merged_stmts)
    execute(module)


if __name__ == "__main__":
    entry = sys.argv[1] if len(sys.argv) > 1 else "example/main.plang"
    run_file(entry)
