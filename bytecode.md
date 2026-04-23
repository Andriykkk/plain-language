# PlainLang Bytecode Design

Design document for the bytecode VM that will replace the tree-walking interpreter.

---

## 1. Goals

- **Fastest VM interpretation** without jumping to full JIT.
- **Clear path to native codegen** (AOT to LLVM IR, or C VM with JIT) without rewriting the bytecode.
- **Typed bytecode from day one** — types are metadata in v1, become enforced and specialized later.
- **No wasted work** — the tree-walker stays as the reference during migration; every test passes through both.

## 2. Strategy — staged path

```
tree-walker (now)  →  SSA-style bytecode in Python  →  VM in C  →  LLVM AOT / JIT
     done                  next (this doc)             later          last
```

Each step ships working code. No "rewrite from scratch" ever.

---

## 3. Execution model — SSA-style register VM

### Register-based, not stack-based

Stack-based (JVM, CPython) needs a de-stackifying pass before native codegen. Register-based (Lua 5+, Dalvik, LuaJIT) maps one-to-one onto CPU registers.

- Register-based is **~30% faster** in interpretation (Lua 4 → Lua 5 gain).
- Register-based maps directly to LLVM IR and CPU instructions — key for the "compile later" goal.

### SSA-style — every instruction produces a value, referenced by index

Like LLVM IR: no explicit `LOADK` for constants, no register allocator in the first pass. Each instruction has an implicit result `%N` where N is its position.

```
%0 = MUL 3, 4              ; constants are immediate operands
%1 = ADD 2, %0             ; previous instruction flows in by index
%2 = SETG "x", %1          ; no LOADK anywhere
```

This is equivalent in runtime performance to allocated registers (same number of slots) but **much simpler compiler** — no register allocation needed for v1. Add allocation later as an optimization pass (like LLVM does).

---

## 4. Operand kinds

Every operand in an instruction is one of:

| Kind | Notation | Meaning |
|---|---|---|
| SSA value | `%N` | result of instruction `N` in current function |
| Constant literal | `42`, `"hi"`, `true`, `3.14` | inline value |
| Named slot | `$name` | local, parameter, or global by name |

Locals and globals live in named slots. SSA values are transient results of instructions. Constants flow freely.

---

## 5. Typed bytecode — design now, enforce later

### Why typed opcodes from day 1

If the bytecode starts with one generic `ADD`, adding types later means splitting every op into N variants — a huge refactor. Starting with typed opcodes (`ADD_I32`, `ADD_F64`, etc.) and routing them all to Python's `+` in v1 costs nothing now and transitions cleanly later.

This is exactly what Java did — `IADD`, `LADD`, `FADD`, `DADD` since 1995.

### Type codes

| Code | Meaning |
|---|---|
| `I32` | 32-bit signed int |
| `I64` | 64-bit signed int (default for `integer`) |
| `F32` | 32-bit float |
| `F64` | 64-bit float (default for `number`) |
| `BOOL` | boolean |
| `TEXT` | UTF-8 string |
| `REF` | heap reference (record, list, map, matrix, function) |
| `NONE` | absence of value |

8 codes, fits in 3 bits. Extensible later (U32, U64, I8/I16 for packed buffers).

### Operand typing

- Every SSA value, local, parameter, and global has a type known at compile time.
- `Function` object carries `local_types`, `param_types`, `return_type`, `ssa_types` (one entry per instruction).
- In v1, the compiler tracks types but doesn't enforce them — the VM dispatches all typed variants to the same Python handler.
- Later, a verifier pass reads the types; a native-codegen backend uses them.

### Explicit conversions — never implicit

Mirror LLVM naming. Conversion is always an explicit opcode:

```
SEXT_I32_I64     sign-extend i32 to i64
TRUNC_I64_I32    narrow i64 to i32
SITOF_I32_F64    signed int to f64
FTOSI_F64_I32    f64 to signed i32
FPEXT_F32_F64    f32 to f64
FPTRUNC_F64_F32  f64 to f32
```

Source `as <type>` desugars to the right conversion. No silent coercion inside arithmetic.

---

## 6. Opcode set

~90 opcodes organized by operand type.

### Arithmetic (per numeric type)

```
ADD_I32  ADD_I64  ADD_F32  ADD_F64
SUB_I32  SUB_I64  SUB_F32  SUB_F64
MUL_I32  MUL_I64  MUL_F32  MUL_F64
DIV_I32  DIV_I64  DIV_F32  DIV_F64
REM_I32  REM_I64                         ; remainder, int only
NEG_I32  NEG_I64  NEG_F32  NEG_F64
```

### Bitwise (int only, add when needed)

```
AND_I32  AND_I64
OR_I32   OR_I64
XOR_I32  XOR_I64
NOT_I32  NOT_I64
SHL_I32  SHL_I64
SHR_I32  SHR_I64                         ; arithmetic (signed) right shift
```

### Comparisons (return BOOL regardless of input type)

```
EQ_I32  NE_I32  LT_I32  LE_I32  GT_I32  GE_I32
EQ_I64  NE_I64  LT_I64  LE_I64  GT_I64  GE_I64
EQ_F32  NE_F32  LT_F32  LE_F32  GT_F32  GE_F32
EQ_F64  NE_F64  LT_F64  LE_F64  GT_F64  GE_F64
EQ_TEXT NE_TEXT                          ; no ordering on text in v1
EQ_BOOL NE_BOOL
EQ_REF  NE_REF                           ; identity compare
```

### Boolean

```
AND_BOOL  OR_BOOL  NOT_BOOL
```

### Literals

```
LOAD_I32_IMM    %r, <i32 immediate>      ; small i32 fits in instruction
LOAD_I64_CONST  %r, <const index>        ; big constants from pool
LOAD_F32_CONST  %r, <const index>
LOAD_F64_CONST  %r, <const index>
LOAD_TEXT_CONST %r, <const index>
LOAD_TRUE       %r
LOAD_FALSE      %r
LOAD_NONE       %r
```

### Typed memory access (records, lists, matrices)

```
LOAD_I32   %r, %base, offset             ; offset = reg or immediate
LOAD_I64   ...
LOAD_F64   ...
LOAD_REF   ...

STORE_I32  %base, offset, %value
STORE_I64  ...
STORE_REF  ...
```

### Allocation — the only memory-creation primitive

```
ALLOC %r, %n_cells                       ; %r = new buffer of n cells
```

### Control flow (type-agnostic)

```
JMP    sBx
JMPF   %cond, sBx                        ; jump if false
JMPT   %cond, sBx                        ; jump if true
CALL   %fn, nargs                        ; args in %fn+1..%fn+nargs, result replaces %fn
RET    %value
RETN                                     ; return none
```

### Globals / locals (by name index)

```
GETG   %r, <name_const_idx>
SETG   <name_const_idx>, %r
; locals (`$name`) are resolved at compile time to slot indices;
; no separate GETL/SETL opcode in SSA-style — locals appear as operands directly
```

---

## 7. What high-level constructs lower to

The VM has no `NEWREC`, `GETF`, `ROWS`, `COLS`, `NEWMAT` opcodes. The compiler lowers them all.

### Records

Compiler assigns field offsets at definition time: `name → 0, age → 1, balance → 2`.

```
set u to new Person         →  ALLOC %0, 3             ; u = alloc(3 cells)
set u.name to "Alice"       →  STORE_REF $u, 0, "Alice"
add 10 to u.balance         →  %1 = LOAD_F64 $u, 2
                               %2 = ADD_F64 %1, 10.0
                               STORE_F64 $u, 2, %2
```

No runtime field name dispatch. Compiler saw `u.balance` and knows it's offset 2.

### Matrices — compile-time shape

Shape `(3, 4)` is **compiler state**, not stored in the value.

```
set g to empty matrix 3 by 4 of number
set g[1, 2] to 99
print g[i, j]
```

```
ALLOC         %0, 12                      ; 3 × 4 = 12 cells
; g[1, 2] = 99 — offset is compile-time: 1*4 + 2 = 6
STORE_F64     $g, 6, 99.0
; g[i, j] — offset is runtime: i*4 + j
%1 = MUL_I64  $i, 4
%2 = ADD_I64  %1, $j
%3 = LOAD_F64 $g, %2
```

`rows of g` → compiler substitutes literal `3`. `columns of g` → `4`. Constant-folded.

### Matrices — runtime shape

Shape packed into the allocation. Layout: `[d1, d2, ..., data...]`.

```
set g to empty matrix r by c of number
```

```
%0 = MUL_I64   $r, $c
%1 = ADD_I64   %0, 2                     ; +2 for shape header
ALLOC          %2, %1
STORE_I64      %2, 0, $r                  ; shape[0]
STORE_I64      %2, 1, $c                  ; shape[1]
SETG           "g", %2
; rows of g → LOAD_I64 $g, 0
; columns of g → LOAD_I64 $g, 1
```

Cost: 2 extra cells per matrix. Trivial.

### Lists

Layout: `{len, cap, data_ptr}` — 3-cell struct.

```
set xs to empty list of number
append 10 to xs
set x to xs[0]
```

```
ALLOC          %0, 3                      ; xs = {0, 0, null}
STORE_I64      %0, 0, 0
STORE_I64      %0, 1, 0
STORE_REF      %0, 2, NONE
SETG           "xs", %0

; append is ubiquitous — keep as an opcode for speed
APPEND         $xs, 10

; xs[0] — load data ptr, index into it
%1 = LOAD_REF  $xs, 2
%2 = LOAD_F64  %1, 0
SETG           "x", %2
```

`APPEND` is the one "cheating" opcode — it's a builtin inline because it's so common.

### Maps

Maps require real hash-table code. They're runtime builtins — no VM changes:

```
NEWMAP     → CALL builtin_map_new
m[k] read  → CALL builtin_map_get
m[k] = v   → CALL builtin_map_set
```

### Strings

Immutable, `{len, bytes}` layout. `length of s` → `LOAD_I64 %s, 0`. Concat and index-by-char are builtins (UTF-8 decode).

---

## 8. Function representation

```python
@dataclass
class Function:
    name: str
    param_types: list[TypeCode]
    return_type: TypeCode
    local_types: dict[str, TypeCode]        # names → types
    ssa_types: list[TypeCode]               # one per instruction, type of its result
    instructions: list[Instruction]
    constants: list[tuple[TypeCode, Any]]   # typed constant pool
    line_info: list[int]                    # source line per instruction (for errors)
```

**Line info is mandatory from day one.** Debugging bytecode without source lines is brutal.

### Calling convention

- Caller puts function reference in `%fn`, args in `%fn+1 .. %fn+nargs`.
- `CALL %fn, nargs` — VM creates a new frame, binds args to the callee's parameter slots.
- Callee's `RET %value` returns to caller; result replaces `%fn`.

This is Lua's convention. Fast, stack-frame-free per call (VM maintains its own call stack).

---

## 9. Instruction format

### In Python (prototype)

```python
@dataclass
class Instruction:
    op: Opcode                      # enum
    operands: tuple[Operand, ...]   # heterogeneous
    line: int                       # for error reporting

@dataclass
class SSA:    index: int
@dataclass
class Const:  type: TypeCode; value: Any
@dataclass
class Local:  name: str
@dataclass
class Global: name_idx: int
@dataclass
class Imm:    type: TypeCode; value: int | float
```

Variable-shape is fine in Python. No encoding tricks.

### In C (later port)

Fixed 32-bit words: `[op:8][A:8][B:8][C:8]` for three-operand ops, `[op:8][A:8][Bx:16]` for jumps.

Operand bit layout: 9-bit operand with MSB indicating "SSA index" vs "constant pool index". (Lua's R/K trick.) When going to C, this replaces the Python object operand with packed indices. Register allocator runs to map SSA indices to a small physical register set.

---

## 10. Value representation

### v1 (Python)

Each register / SSA slot / local holds a Python object. Type is tracked via `ssa_types`, `local_types`, etc., on the `Function`. Runtime doesn't check; bugs surface as Python `TypeError`.

### v2 (C)

**Tagged union:** 16-byte struct per value.

```c
typedef struct {
    uint8_t  tag;        // TypeCode
    uint64_t payload;    // i64, f64 (bits), or pointer
} Value;
```

Simple, portable. Upgrade to **NaN-boxing** (8 bytes, LuaJIT/SpiderMonkey style) as a later optimization — not a starting design.

### v3 (when types are enforced)

Typed slots — no tag, because every slot's type is known statically. Raw i32/i64/f32/f64/ptr per register. This is what native codegen wants.

---

## 11. Compilation flow

```
source text          (user input)
   │ lexer            (done)
   ▼
tokens
   │ parser           (done)
   ▼
AST
   │ compiler.py      ← new: walk AST, produce Function objects
   ▼
Module (functions, constants, record types, line info)
   │ vm.py            ← new: dispatch loop, frame management
   ▼
output
```

**Single pass, no separate IR.** The SSA-style bytecode *is* the IR — that's the LLVM approach. Adding an intermediate IR layer is needed only when nontrivial optimization passes appear.

### What the compiler does

One class, ~400 lines. Two key patterns:

**Statements emit instructions (side effects):**
```python
def compile_stmt(self, stmt):
    if isinstance(stmt, SetStmt):
        value = self.compile_expr(stmt.value)       # returns an Operand
        self.emit(SETLOCAL, stmt.target.name, value)
    elif isinstance(stmt, IfStmt):
        cond = self.compile_expr(stmt.condition)
        jmp_false = self.emit(JMPF, cond, PLACEHOLDER)
        for s in stmt.then_block: self.compile_stmt(s)
        self.patch(jmp_false, self.current_pos())
```

**Expressions emit instructions and return an Operand:**
```python
def compile_expr(self, expr) -> Operand:
    if isinstance(expr, NumberLit):      return Const(I64, expr.value)
    if isinstance(expr, VarRef):         return Local(expr.name)
    if isinstance(expr, BinaryOp):
        left  = self.compile_expr(expr.left)
        right = self.compile_expr(expr.right)
        return self.emit_ssa(ADD_I64, left, right)  # returns SSA(%N)
```

`emit_ssa` appends the instruction, records its output type, returns `SSA(index)`. That's it — no scheduling, no register allocation.

### What the compiler tracks

- Record field offsets per record type
- Matrix shape (when known at compile time; otherwise the packed layout)
- Type of each SSA value, local, parameter, global
- String interning table (field names, global names → constant-pool indices)
- Line numbers (AST → instruction)

---

## 12. Implementation plan

### Phase 1 — Types & data (1 evening)

**`bytecode.py`** — pure data:
- `Opcode` enum (~90 values)
- `TypeCode` enum (8 values)
- `Instruction` dataclass
- Operand types (`SSA`, `Const`, `Local`, `Global`, `Imm`)
- `Function`, `Module` dataclasses

No logic. Just shapes.

### Phase 2 — Minimal subset compiler + VM (a few evenings)

Implement compile+VM for this subset only:
- Literals (`NumberLit`, `StringLit`, `BoolLit`, `NoneLit`)
- `set <name> to <expr>`, `add`/`subtract`/`multiply`/`divide`
- `print`
- Arithmetic expressions (`+`, `-`, `*`, `/` and word equivalents)
- `if` / `else` / `end`
- `repeat for i from X to Y ... end`
- Functions (`define function`, `return`, `call`)

**Skip for now:** records, lists, maps, matrices, `repeat for each`, `repeat while`, comparisons except the few `if` needs.

VM only needs ~20 opcodes for this subset. Run the relevant subset of `tests/test_interpreter.py` through it. Match tree-walker on every test.

### Phase 3 — Records

Field-offset tracking in compiler. `ALLOC + LOAD + STORE` lowering. About 100 lines of compiler logic; no VM changes (opcodes already exist).

### Phase 4 — Lists + `APPEND`

List struct lowering. `APPEND` opcode added to VM. Builtins for map-like ops.

### Phase 5 — Matrices

Shape tracking (constant-fold when possible, packed layout when runtime). No new VM opcodes; all lowers to arithmetic + LOAD/STORE.

### Phase 6 — Everything else

`repeat for each`, `repeat while`, full comparisons, strings as structs, maps via builtins.

At each phase, all existing tests must pass on both interpreter and VM. Delete the tree-walker only when every test runs green on the VM for a sustained period.

---

## 13. Example — full function in bytecode

Source:
```
define function sum_to
    input n as i64
    output as i64

    set total to 0
    repeat for i from 0 to n - 1
        add i to total
    end
    return total
end
```

Bytecode (informal, pre-encoding):
```
function sum_to(I64 n) -> I64
  locals: total : I64, i : I64
  constants: [#0: I64 0, #1: I64 1]

  SETLOCAL    $total, 0
  SETLOCAL    $i, 0
.loop:
  %0 = SUB_I64   $n, 1
  %1 = GT_I64    $i, %0
       JMPT      %1, .end
  %2 = ADD_I64   $total, $i
       SETLOCAL  $total, %2
  %3 = ADD_I64   $i, 1
       SETLOCAL  $i, %3
       JMP       .loop
.end:
       RET       $total
```

Every instruction maps roughly 1:1 to an x86 / LLVM IR instruction when you eventually lower. The `LOADK` noise is gone; constants and locals flow as operands. Types are stated.

---

## 14. Things to defer

| Feature | Defer until |
|---|---|
| NaN-boxing | C port, after tagged union works |
| Register allocation | When SSA-slot memory footprint matters (C port) |
| Inline caches for field / global lookup | After basic VM; 2–5× wins |
| Peephole optimizer | After VM passes all tests |
| JIT / native codegen | Last phase. Ignore until everything else works. |
| Garbage collection | C port only (Python's GC serves for v1) |
| Closures / upvalues | When nested functions are added |
| Coroutines | Only if desired |
| Exception handling | Desugar control flow to jumps; real exceptions later |
| String interning | C port (Python already does it) |
| FFI / C API | After C VM exists |
| Bytecode serialization | After VM is stable |

---

## 15. Things to decide before writing code

- **Overflow semantics** for integer arithmetic — wrap (Java) or trap (Rust debug)? Pick when `I32`/`I64` ops are actually implemented. Pragmatic default: wrap.
- **Float NaN equality** — IEEE (NaN != NaN) or source-language equality? IEEE is standard; default to it.
- **Default type for unannotated `number`** — `F64` (Lua's choice), or `I64` for integer literals and `F64` for decimals? Pragmatic default: integer literals → `I64`, decimal literals → `F64`.
- **Variable type reassignment** — `set x to 5; set x to "hi"`? Once static typing is on, forbid it. In v1 (dynamic), allow it. The bytecode compiler in v1 tracks the latest-assigned type for each local.

---

## 16. Path to native speed

Once the Python VM passes the full test suite:

1. **Port VM to C** with computed-goto dispatch. ~10–20× speedup over Python VM. Add register allocator at this point (reuse SSA slots). Tagged-union values.

2. **LLVM AOT backend.** Because bytecode is already SSA, each function lowers nearly directly to LLVM IR. Each typed opcode → one LLVM instruction:
   - `ADD_I64` → `add i64 %a, %b`
   - `ADD_F64` → `fadd double %a, %b`
   - `LOAD_I64` → `load i64, ptr %p`
   - `SITOF_I64_F64` → `sitofp i64 %a to double`

3. **Optional: tracing JIT** (LuaJIT-style) if AOT isn't flexible enough for dynamic hot paths.

Steps 1–2 get you within 2–5× of native C speed. Step 3 closes the gap where needed.

---

## 17. File layout

```
bytecode.py         # opcode enum, type codes, Instruction, Function, Module
compiler.py         # AST → Module. ~400 lines.
vm.py               # dispatch loop, frames, call stack. ~300 lines.
evaluator.py        # KEEP as reference interpreter during migration.
tests/              # existing tests run against both; VM must match tree-walker.
```

When the VM is green on every test for a month, delete the tree-walker.
