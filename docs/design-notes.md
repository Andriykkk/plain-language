# PlainLang Design Notes

Working notes on a human-readable programming language with compiler-chosen data structures and GPU-style parallelism.

---

## 1. Analysis of the Original Idea

The original sketch fuses three different ideas. Each has a different risk level.

### Idea A — Human-readable syntax (COBOL/ABC-style)
**Risk: high.** Research (Stefik & Siebert, 2013) shows English-keyword syntax gives beginners no advantage over `{}`-style. AppleScript is the canonical cautionary tale: `set x to 5` reads fine until nested constructs make the English harder than symbols. Inform 7 is the only genuine NL-like success — works because interactive fiction is a narrow domain where NL descriptions *are* the domain.

### Idea B — Intent-based data structure selection
**Risk: medium, but promising.** Real prior art:
- SQL query planners (pick index / join strategy from intent)
- Haskell's stream/list fusion
- Halide — separates *algorithm* from *schedule*
- Futhark — picks parallel execution strategies from array primitives

The Halide insight: don't hide structure choice behind "collection with fast lookup"; make it a *separate, overridable* declaration. This should be the central design principle.

### Idea C — GPU / data-parallel blocks
**Risk: low novelty.** What's described is just `map` / `reduce` / `transform` over arrays — already done by Futhark, JAX, NumPy, Julia's KernelAbstractions, Halide. No novelty unless syntax unlocks something specific.

### Unresolved tensions in the original sketch
- **Two syntaxes for everything** (`set total to total + 10` vs `increase total by 5`). Smell. Pick one.
- **Who is the user?** Beginners, data scientists, domain experts — each demands a different language.
- **Error messages.** English syntax makes errors *worse* — "expected noun phrase after 'to'" is horrifying.
- **Tooling, libraries, FFI.** The reason ABC died.

### Honest recommendation
Drop general human-readable syntax as the main selling point. Keep intent-vs-implementation separation as the central design principle. Pick one niche — the most promising is **a readable front-end for parallel array/data processing**, competing with NumPy/Pandas scripts written by non-programmer analysts.

---

## 2. What Halide Is and How It Works

A DSL embedded in C++ for high-performance image/array processing. Created at MIT ~2012 by Jonathan Ragan-Kelley.

### Key idea: separate algorithm from schedule
*What* you compute and *how* you compute it are separate programs. The algorithm stays mathematical; the schedule (tiling, vectorization, parallelism) is tuned independently without risking correctness.

### Core concepts
- **`Func`** — function from N-D coordinates to a value
- **`Var`** — a loop variable
- **`RDom`** — reduction domain (for sums, histograms, convolutions)
- **`Buffer`** — actual memory (input or output)

### Example: 3×3 box blur

Algorithm:
```cpp
Var x, y;
Func blur_x, blur_y;
blur_x(x, y) = (input(x-1, y) + input(x, y) + input(x+1, y)) / 3;
blur_y(x, y) = (blur_x(x, y-1) + blur_x(x, y) + blur_x(x, y+1)) / 3;
```

Schedule (CPU):
```cpp
Var xi, yi;
blur_y.tile(x, y, xi, yi, 256, 32)
      .vectorize(xi, 8)
      .parallel(y);
blur_x.compute_at(blur_y, x).vectorize(x, 8);
```

Schedule (GPU) — same algorithm, different schedule:
```cpp
blur_y.gpu_tile(x, y, xi, yi, 16, 16);
blur_x.compute_at(blur_y, Var::gpu_blocks());
```

### Pipeline
1. Define Funcs
2. Define schedule
3. Compile (JIT or AOT, via LLVM, to x86/ARM/CUDA/Metal)
4. Run with buffers

### Where it's used
Google Android camera, Photoshop, Adobe. Real production system.

---

## 3. What Futhark Is and How It Works

Standalone purely-functional statically-typed array language. Compiles to CUDA, OpenCL, or parallel C. University of Copenhagen, ongoing since ~2014.

### Key idea: parallelism from array primitives
Small set of parallel combinators (`map`, `reduce`, `scan`, `filter`, `scatter`); compiler turns them into efficient GPU kernels and **fuses** adjacent operations so intermediates don't materialize.

### Core primitives
- `map f xs` — apply to every element
- `reduce op ne xs` — combine with associative `op`, neutral `ne`
- `scan op ne xs` — parallel prefix operation
- `filter p xs` — keep matching elements
- `scatter dest is vs` — indexed writes

### Example: sum of squares
```futhark
let sum_of_squares (xs: []i32): i32 =
  let squares = map (\x -> x * x) xs
  in reduce (+) 0 squares
```
Fused into one GPU kernel — no intermediate `squares` array is allocated.

### Example: matrix multiply (nested parallelism)
```futhark
let matmul [n][m][p] (A: [n][m]f32) (B: [m][p]f32): [n][p]f32 =
  map (\row ->
    map (\col ->
      reduce (+) 0 (map2 (*) row col)
    ) (transpose B)
  ) A
```
Size types (`[n][m]`) catch dimension mismatches at compile time.

### Compilation stages
1. Parse + type-check with size types
2. IR: SOACs (Second-Order Array Combinators)
3. **Fusion** — merge adjacent maps/reductions
4. **Flattening** — nested parallelism → flat GPU-friendly parallelism
5. Codegen: CUDA / OpenCL / C

### Why it's fast
- Fusion eliminates GPU global-memory roundtrips (the slowest thing)
- Flattening maps nested parallelism onto GPU block/thread hierarchy
- Moderate-parallelism inference decides which loops to parallelize vs. sequentialize

---

## 4. Halide vs. Futhark — The Philosophical Split

Same problem ("blur an image fast on GPU"):
- **Halide:** You write the algorithm, you tell the compiler the strategy. Scalpel for experts.
- **Futhark:** You write the algorithm as nested `map`/`reduce`. Compiler picks strategy. Reliable auto-transmission.

Halide trusts the human to schedule. Futhark trusts the compiler. **PlainLang's design point: default like Futhark, override like Halide.** Legitimate, but doubles the compiler work.

---

## 5. Applying Both Ideas in PlainLang

Neither idea requires functional purity or C++ embedding. Halide is embedded in C++ by convenience. Futhark is pure-functional because it's easier to implement. You can get ~80% of both in an imperative, human-readable language if you accept **local restrictions inside parallel blocks** instead of a global purity rule.

### 5.1 Halide-style pipeline + schedule

```
define pipeline blur over image
    stage blur_x at (x, y)
        compute (image[x-1, y] + image[x, y] + image[x+1, y]) / 3
    end

    stage blur_y at (x, y)
        compute (blur_x[x, y-1] + blur_x[x, y] + blur_x[x, y+1]) / 3
    end

    output is blur_y
end

schedule blur
    tile blur_y by (256, 32)
    vectorize blur_y inner x by 8
    parallel blur_y over y
    compute blur_x inside each tile of blur_y
end
```

Run:
```
set result to run blur on input_image
```

GPU schedule — same pipeline, different schedule:
```
schedule blur on gpu
    tile blur_y by (16, 16)
    map tiles to gpu blocks
    map inner (x, y) to gpu threads
end
```

### 5.2 Rules that make stages compilable
Each `stage ... at (x, y) compute <expr>`:
- Function of coordinates only
- No mutation of outside state
- No I/O
- Can reference earlier stages by indexing

Outside stages, the language stays imperative. Restrictions are local. This is how OpenMP, CUDA C, and ISPC work.

### 5.3 Futhark-style parallelism without going pure functional

Three primitives + restricted bodies.

**`transform` (map):**
```
transform numbers into squares
    using item times item
end
```

**`reduce`:**
```
reduce numbers into total
    starting from 0
    using accumulator plus item
    combine with plus
end
```
Combiner must be associative. Compiler auto-accepts known associative ops (`plus`, `times`, `min`, `max`, `and`, `or`); anything else requires explicit assertion.

**`scan` (prefix):**
```
scan balances into running_total
    starting from 0
    using accumulator plus item
end
```
Looks sequential, compiles parallel. Enables parallel parsing, compaction, sorting.

**`filter`:**
```
filter numbers into positives
    where item is greater than 0
end
```

### 5.4 Nested parallelism works
```
transform matrices into row_sums
    using
        reduce row into row_total
            starting from 0
            using total plus item
        end
        return row_total
    end
end
```
v1: parallelize only the outermost level (what most languages do, it's fine).
Later: flattening transformation for nested GPU parallelism.

### 5.5 Restrictions inside parallel blocks
Inside `transform`, `reduce`, `scan`, `filter`, `stage`, or `on gpu`:
1. No writes to variables declared outside the block (except the designated output)
2. No I/O, no `print`, no side-effecting function calls
3. No early `return` from the enclosing function
4. Indexed reads from outside arrays are fine

Outside parallel blocks, stay fully imperative. Same discipline as Rust `par_iter`, Julia `@threads`, C# `Parallel.For`.

---

## 6. What's Achievable vs. What's Hard

### Relatively cheap
- Algorithm/schedule split for array pipelines
- Parallel `transform` / `reduce` / `scan` / `filter` on CPU with threads
- Usable language for ~80% of data-parallel work

### Still genuinely hard
- GPU codegen (CUDA/OpenCL/SPIR-V) — months even with LLVM help
- Nested-parallelism flattening — PhD-level; punt initially
- Fusion (combining adjacent `transform`s so intermediate arrays don't materialize) — Futhark's single biggest optimization
- Good error messages when code looks parallel-safe but isn't

---

## 7. Suggested Build Order

1. **Imperative core + parser.** `set`, `if`, `repeat`, functions. Interpret in Python or Rust. No parallelism yet.
2. **`transform` / `reduce` / `scan` / `filter` as CPU ops.** Sequential first, then multi-threaded. Enforce restriction rules at parse time.
3. **Pipeline / stage syntax.** Compile to nested loops first, no scheduling.
4. **Schedule language.** Start with only `parallel` and `tile`. Get those right.
5. **LLVM backend.** When the interpreter is proven, lower to LLVM IR for CPU performance.
6. **GPU.** Only after everything else works. Most projects die here; no shame in stopping at step 5.

### Honest effort estimate (one person)
- Steps 1–3: a few months of serious work
- Step 4: a few more months
- Steps 5–6: years

---

## 8. Things to Read Before Writing a Parser

- Halide paper — Ragan-Kelley et al., PLDI 2013: *"Halide: A Language and Compiler for Optimizing Parallelism, Locality, and Recomputation in Image Processing Pipelines"*
- Futhark paper — Henriksen et al., PLDI 2017: *"Futhark: Purely Functional GPU-Programming with Nested Parallelism and In-Place Array Updates"*
- Stefik & Siebert 2013 — empirical study on syntax and beginners
- Inform 7 documentation — the one successful NL-like language; study *why*
