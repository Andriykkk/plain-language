[ ] add list initialisation
[ ] rows of / columns of only make sense for 2D+. For 3D+, you'd want size of m at dimension N or a shape of m returning a list. Leaving for when you actually need it.
All matrix element types go into a Python list right now. When you get to the C-backend stage, matrix of number is the one that should specialize to a contiguous typed array for real performance. The AST already carries elem_type, so that's pure codegen work — no user-facing change.
Matrices are not growable (by design). If you later want appendable fixed-width rows, that's a different container (buffer? grid?) — don't overload matrix


[ ] Bitwise ops on integers (and, or, xor, <<, >>) — not parsed yet in the language either.
No runtime type verification — the compiler tracks types, but bad bytecode (e.g., hand-constructed) wouldn't be rejected.
No call-stack depth limit — deep recursion will hit Python's recursion limit.


[ ]
No I8, I16, or unsigned types. Same opcode family pattern — add when needed.
No overflow checking. Python ints don't overflow; the compiler's type contract is the only guarantee. A C port would need real-wraparound semantics.
No comparison opcodes yet (EQ_I64, LT_F64, etc.). Same structure; add with the if/loop work.
Dump shows literal types as "I64"/"F64" for unlabeled cells. The compiler's actual symbol_types is authoritative for variables (and correct); the _guess_type fallback only applies to anonymous constants.
Constants aren't deduplicated. Same literal appearing twice allocates twice. Easy optimization for later.
Matrix element types aren't tracked — reading m[i, j] still returns REF (opaque). Will matter when arithmetic on matrix cells needs specific-typed opcodes; straightforward extension of symbol_shapes to carry element type too