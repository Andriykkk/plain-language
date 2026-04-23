Hybrid GC + scoped allocation — a real design, but think through these first
What you're describing is a well-known model: GC by default, user-declared "local scope" for deterministic cleanup. Variants exist in D (@nogc), Nim (arenas), Odin (context allocators), and partially in Go (escape analysis, defer). It's workable. But there are decisions packed inside "skip GC and be cleared inside function" that you should untangle.

Two different things you might be merging
Stack allocation — value lives on the call stack, dies at return. Zero cost. Cannot be returned or stored outside the function. The compiler usually decides this via escape analysis, not the user.

Function-scoped arena — heap memory, but the whole chunk is freed in one shot when the function ends. Can grow dynamically, can hold many objects, can be mutated freely within the function. User opts in explicitly.

These behave differently and need different syntax and different restrictions. Go's defer is neither of these — it's a callback that runs at function exit, mostly used for closing files and unlocking mutexes, not for memory.

You probably want all three eventually: stack (compiler-decided), arena (user-declared scope), and defer (for non-memory resources). Don't collapse them into one concept.

For the interpreter phase: ignore memory entirely
Python already has GC. Your interpreter just hands memory to Python's runtime. You get GC for free, you don't implement anything, and you don't have to commit to a memory model while you're still iterating on syntax.

The only memory decision worth making now: semantics of assignment and passing. Specifically, when a user writes set a to b, is that a copy or a reference? This is observable in a GC language — it changes what user programs mean. Decide:

Value types (number, text, small records) → copied. Like Go structs.
Reference types (lists, maps, large records) → shared reference. Like Go slices/maps, Python lists.
Explicit deep copy via a copy operation when user wants it.
That decision locks in semantics for users. You can't change it later without breaking programs.

For the compiled phase: staged commitment
Add complexity only when you have data showing it's needed. Reasonable staging:

v1 compiled: pure GC, like Go. One memory model. defer for closing files, unlocking locks — not for memory. Simple for users, simple to implement. Go proves this is enough for huge real systems.

v2: compiler escape analysis. No user syntax. The compiler notices when a local allocation doesn't escape and stack-allocates it automatically. Free win, zero user complexity. Go does this.

v3: explicit scope blocks for arenas, if profiling shows GC pressure is the bottleneck in real programs. Something like:


scope
    set buffer to list of number
    ...
end
Everything allocated inside the scope is freed at end. Cannot escape the scope — enforced by the type system. This is the "skip GC" path you're describing, done cleanly.

Skipping steps 1–2 and jumping straight to user-visible escape hatches is how memory bugs and language complexity multiply. Go the Go route, then specialize only where you have evidence.

About defer specifically
Go's defer is valuable in a GC language precisely because memory isn't its job. It's for:

Closing files, sockets, database connections
Unlocking mutexes
Restoring global state (signal handlers, logging level)
Calling user-defined cleanup on opaque foreign resources
If you only ever use defer for freeing memory, you've built something GC would have handled anyway. The real use case is non-memory resources with deterministic cleanup. That's the pitch.

Syntactically, Go's model is solid: defer <call> stacks up; calls run LIFO at function exit, even on panic. Worth copying directly.

The risk you're taking on
Every language that added a "manual memory" escape hatch — D, Nim, even C# with unsafe — split its community and complicated its compiler. GC-only languages (Go, OCaml, Java) stayed simpler and still handle production workloads. Unless your target audience has specific real-time or embedded needs, GC-only for v1 is the right call. You can always add arenas in v2.