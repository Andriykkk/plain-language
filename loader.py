"""
Module loader.

Walks the import graph from the entry file, resolves paths, parses each
file's import block to discover dependencies, detects cycles, and
produces a topologically-sorted list of files for the compile pipeline.

For now this file just hosts the language's file-extension constant —
all source files end in `.plang`. The actual graph-walking machinery
arrives in the next step.
"""

# All language source files use this extension. Import paths are
# written without it: `import "utils"` resolves to `utils.plang`,
# `import "helpers/strings"` resolves to `helpers/strings.plang`.
LANG_EXTENSION = ".plang"
