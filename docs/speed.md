"Maximum speed + dynamic-feeling language" is a solved problem — it's called static typing with macros
What you're describing isn't actually a dynamic language. It's a statically-typed language with compile-time extensibility. The canonical examples:

Julia — looks dynamic, compiles each typed specialization to LLVM native. Fast as C for type-stable code. Has macros for syntax extension.
Nim — static types, Python-ish syntax, compiles to C, has AST macros for user syntax extension.
Zig — strict types, manual allocators, comptime metaprogramming.
Rust — strict types, procedural macros.
Your earlier define operation "add <a> and <b>" pattern system maps cleanly onto compile-time macros, not runtime dispatch. That's the insight that unlocks speed.

The two-phase model

Source → [Expand patterns] → [Type check] → [Compile] → Native code
             compile time       compile time    compile time    runtime
Everything extensible happens before execution. By the time code runs, all add a and b calls have been resolved to concrete typed functions with known machine representations. No runtime pattern lookup, no dynamic dispatch, no boxing.

This is how Julia hits C speed despite looking dynamic — the dispatch happens at JIT time, not per-call.

What "dynamic values" actually means in a fast language
You don't need Python-style runtime typing. You need tagged unions (also called sum types, variants, enums with data):


define variant Value
    is number
    is text
    is list of Value
end
Internally that's a struct with a tag byte + payload — fixed size, known layout, zero boxing. Rust enum, Zig union(enum), Haskell ADT. You get "this variable could be one of several things" without Python's overhead.

Growing arrays (Vec<T>, ArrayList<T>) are also compatible with strict typing. list of number compiles to a {ptr, len, cap} struct — exactly what C++ std::vector is.

For maximum speed, an interpreter will not cut it
Honest numbers vs. C:

Tree-walking interpreter in Python: 50–500× slower
Bytecode VM in Python: 20–100× slower
Bytecode VM written in C: 3–10× slower
AOT compile to C/LLVM: 1–2× slower, sometimes faster
JIT (LuaJIT-class): 1–2× slower, multi-year engineering effort
If "maximum speed" is non-negotiable, the end state is AOT compile to C or LLVM IR. There's no shortcut.

But build the interpreter first anyway
Every successful language did this:

Nim: Pascal prototype → self-hosted C backend
Rust: OCaml prototype → self-hosted
Julia: Scheme prototype → LLVM
Why: you're still designing the syntax and semantics. If you write a compiler first, every language change forces codegen rewrites. An interpreter lets you iterate the design at 10× speed. Once it's stable, the compiler is mechanical translation.

Realistic path:

Tree-walking interpreter in Python — days to weeks. Nail down semantics.
Compile to C — months. Now it's fast. Nim's approach. C compilers do the hard optimization work for free.
Compile to LLVM IR — optional later. Better optimizer, harder toolchain.
Do not try to skip step 1. The number of dead language projects that tried is enormous.

Key design decisions you must commit to early
These all affect both the interpreter's shape and the eventual compiler. Decide now:

Memory model — pick one, they're not combinable:

Garbage collection (easy, non-deterministic pauses). Go, OCaml, Java.
Reference counting (predictable, slower, cycle problems). Swift, Python.
Ownership/borrows (fastest, hard to design, years of work). Rust.
Manual + allocators (fastest, hardest for users). Zig, C.
For a readable COBOL-ish language, GC is the right default. You can add arenas later for hot code.

String model:

text = immutable UTF-8 bytes, length-prefixed. Like Go, Rust &str.
Indexing by code point is O(n), byte indexing is O(1). Users see code points.
No null-terminated strings, no encoding ambiguity.
Array model:

list of T = {ptr, length, capacity}. Growable. Amortized O(1) append.
Indices are bounds-checked (cheap; can be elided in tight loops later).
Pass by reference by default; explicit copy when needed.
Number model:

number = 64-bit float, or split into integer (64-bit signed) + decimal (64-bit float)?
COBOL audiences want fixed-point decimal for money. Worth considering money as a first-class type.
Variant (sum type) representation:

Tagged union, tag-first, fixed size = size of tag + size of largest variant.
Exhaustive pattern matching at compile time: when value is number ... when value is text ... end.
Concrete recommendation
Build the interpreter in Python this week. No speed concerns — it's for design iteration.
Commit to: GC, UTF-8 strings, growable lists, tagged unions for variants, compile-time pattern expansion.
When the language runs real programs and syntax has been stable for ~a month, start the C backend.
Leave LLVM for after the C backend works.
That gets you from zero to "fast, statically typed, extensible, readable language" in ~6-12 months of serious part-time work. Starting with a compiler gets you to "abandoned prototype" in ~3 months.