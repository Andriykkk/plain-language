[ ] add to vm map and 3d matrices
[x] add "and" and "or"
[x] remake benchmarks
[x] add type conversions
[ ] add imports
[ ] implement few libraries to look like real language
[ ] rows of / columns of only make sense for 2D+. For 3D+, you'd want size of m at dimension N or a shape of m returning a list. Leaving for when you actually need it.


[X] Bitwise ops on integers (and, or, xor, <<, >>) — not parsed yet in the language either.
Bitwise ops on integers — both C-style symbols and word forms supported.
- Symbols: & | ^ ~ << >>
- Words:   bit and / bit or / xor / bit not / shifted left by / shifted right by
- Logical and/or/not stay reserved for booleans (Python-style).
- Reject bitwise on floats at compile time.
- Precedence: shifts > AND > XOR > OR; all below addition.


[x] 
Maps — empty map of text to number, set/get by key, length. No opcodes exist. Unblocks ~4 tests.

# improves
[ ] add separate functions to compare lists, matrices, fill them, etc
[ ] add pointer values, to set records to list and properly print them(special variable in compiler know pointer size)
[ ] add list initialisation
[ ] make better functions input and output
[ ] add debuger and parser to show what types during coding

# future
[ ] add array of structures or structure of arrays to choose when allocate
[ ] add machine learning to predict result of the code to choose best optimisation
[ ] add slow and dynamic value
[ ] add more types https://www.youtube.com/watch?v=X40rcpLfMdY&list=WL&index=3&pp=iAQBsAgC
