[ ] add list initialisation
[ ] make better functions input and output
[ ] add slow and dynamic value
[ ] add to vm map and 3d matrices
[ ] add debuger and parser to show what types during coding
[ ] add separate functions to compare lists, matrices, fill them, etc
[ ] add divide everything for matrix and 
[x] add "and" and "or"
[ ] add pointer values, to set records to list and properly print them(special variable in compiler know pointer size)
[ ] add type conversions
[ ] add comptime list length check, not complex, just where it possible
[ ] add more types https://www.youtube.com/watch?v=X40rcpLfMdY&list=WL&index=3&pp=iAQBsAgC
[ ] add array of structures or structure of arrays to choose when allocate
[ ] add machine learning to predict result of the code to choose best optimisation
[ ] rows of / columns of only make sense for 2D+. For 3D+, you'd want size of m at dimension N or a shape of m returning a list. Leaving for when you actually need it.
All matrix element types go into a Python list right now. When you get to the C-backend stage, matrix of number is the one that should specialize to a contiguous typed array for real performance. The AST already carries elem_type, so that's pure codegen work — no user-facing change.
Matrices are not growable (by design). If you later want appendable fixed-width rows, that's a different container (buffer? grid?) — don't overload matrix


[X] Bitwise ops on integers (and, or, xor, <<, >>) — not parsed yet in the language either.
Bitwise ops on integers — both C-style symbols and word forms supported.
- Symbols: & | ^ ~ << >>
- Words:   bit and / bit or / xor / bit not / shifted left by / shifted right by
- Logical and/or/not stay reserved for booleans (Python-style).
- Reject bitwise on floats at compile time.
- Precedence: shifts > AND > XOR > OR; all below addition.


[ ] 
Maps — empty map of text to number, set/get by key, length. No opcodes exist. Unblocks ~4 tests.
3D matrix — empty matrix 2 by 2 by 2. compile_set_matrix hardcodes len(shape) != 2 rejection compiler.py:331-332.