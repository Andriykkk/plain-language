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


[ ]
Compound assignments (add N to x, subtract, multiply, divide)	No handler for AddStmt etc.	~15 tests
All repeat loop forms (N times, for i from X to Y, while, for each)	No loop compiler	~15 tests
stop / skip	Same (needs loop context stack)	2 tests
Functions (define function, call, return)	No function compiler	~10 tests
Maps beyond basics	empty map / indexed ops partial	3 tests
Record default field values / end record style closer / nested field access	Partial	~5 tests
3D+ matrices	Only 2D supported	1 test
print a and b joining with space	compile_print emits one PRINT per part, each adds newline	2 tests
