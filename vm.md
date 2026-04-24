Design for typed arithmetic — four new types, promotion, explicit conversions
Before implementing, here's the concrete plan. It adds ~30 opcodes and ~100 lines of compiler logic, but the structure is mechanical.

1. Type system
Add to TypeCode:

Code	Size	What
I32	4 bytes	32-bit signed integer
I64	8 bytes	64-bit signed integer (current default)
F32	4 bytes	32-bit float
F64	8 bytes	64-bit float (current default)
Keep BOOL, TEXT, NONE, REF as-is.

Literal defaults:

5 → I64 (integer literal)
3.14 → F64 (decimal literal)
"hello" → TEXT
true → BOOL
Source-level type names (recognized in typeref_to_code):


i32 → I32         f32 → F32
i64 → I64         f64 → F64
integer → I64     (default int alias)
float → F64       (default float alias)
number → F64      (kept for compat)
2. Type annotations — parser change
Extend set to accept an optional as <type> suffix:


set x to 5              # inferred I64
set y to 5 as i32       # explicit I32 — value is converted
set z to 3.14 as f32    # explicit F32
Parser change (tiny): parse_set optionally consumes as <type>. SetStmt gains an annotated_type: TypeRef | None field. Default None.

The compiler handles the annotation by inserting a conversion from the inferred value type to the annotated type.

3. Typed arithmetic opcodes
Replace the single ADD, MUL with typed variants:


ADD_I32  ADD_I64  ADD_F32  ADD_F64
SUB_I32  SUB_I64  SUB_F32  SUB_F64
MUL_I32  MUL_I64  MUL_F32  MUL_F64
DIV_F32  DIV_F64                       ; division → float only
14 arithmetic opcodes. Each is one line in the VM (Python's native + / - / * / /).

No DIV_I32 / DIV_I64. Integer division always promotes both operands to F64 first, then uses DIV_F64. This matches Python 3's / behavior and the user's stated rule ("integer divided result is float it is ok").

4. Conversion opcodes
One per ordered pair of distinct numeric types. 4 × 3 = 12 conversions:


# int ↔ int
CVT_I32_I64        CVT_I64_I32
# float ↔ float
CVT_F32_F64        CVT_F64_F32
# int → float
CVT_I32_F32        CVT_I32_F64
CVT_I64_F32        CVT_I64_F64
# float → int
CVT_F32_I32        CVT_F32_I64
CVT_F64_I32        CVT_F64_I64
Each is registers[dst] = int(registers[src]) or float(...) in the Python VM. In a future C port, these become real trunc, sext, fptosi, sitofp LLVM instructions.

5. Promotion rules
When ADD sees two different-typed operands, the compiler promotes to the "smallest common type that covers both":

Left	Right	Result
I32	I32	I32
I64	I64	I64
I32	I64	I64 (widen I32)
F32	F32	F32
F64	F64	F64
F32	F64	F64 (widen F32)
I32	F32	F32
I32	F64	F64
I64	F32	F64 (F32 can't represent all I64 exactly — upgrade to F64)
I64	F64	F64
Order doesn't matter (promote is commutative). For DIV override: result is always F64, promote both operands to F64.

6. Compiler changes
compile_binop (new)
The current compiler has no arithmetic-expression handling — BinaryOp isn't in compile_expr_into yet. This pass adds it.


compile_binop(expr, reg):
    left_reg, right_reg = reg+1, reg+2
    left_type  = compile_expr_into(expr.left,  left_reg)
    right_type = compile_expr_into(expr.right, right_reg)

    if expr.op == "divided":
        result_type = F64
    else:
        result_type = promote(left_type, right_type)

    if left_type  != result_type:
        emit(CONVERT[left_type → result_type], left_reg, left_reg)
    if right_type != result_type:
        emit(CONVERT[right_type → result_type], right_reg, right_reg)

    emit(ARITH_OPCODE[op, result_type], reg, left_reg, right_reg)
    return result_type
Type-tracking helpers

promote(a, b) → TypeCode           # table lookup
arith_opcode(op_word, type) → Opcode    # e.g. ("plus", I32) → ADD_I32
convert_opcode(from, to) → Opcode       # e.g. (I32, F64) → CVT_I32_F64
emit_convert(from, to, src, dst)        # uses convert_opcode + emit
compile_set with annotation

value_type = compile_expr_into(stmt.value, reg=0)
if stmt.annotated_type is not None:
    target_type = typeref_to_code(stmt.annotated_type)
    if value_type != target_type:
        emit_convert(value_type, target_type, src=0, dst=0)
    value_type = target_type
# ...store to slot with type=value_type
Matrix index math (already uses ADD/MUL)
Update to use ADD_I64 and MUL_I64 specifically — indices are conceptually I64 in the VM.

7. VM changes
One dispatch branch per new opcode. Each is a single line:


elif op is Opcode.ADD_I64: registers[r_dst] = registers[r_a] + registers[r_b]
elif op is Opcode.ADD_F64: registers[r_dst] = registers[r_a] + registers[r_b]
# ...
elif op is Opcode.CVT_I64_F64: registers[r_dst] = float(registers[r_src])
elif op is Opcode.CVT_F64_I64: registers[r_dst] = int(registers[r_src])
In Python, I32 and I64 are the same underlying type — so ADD_I32 and ADD_I64 both do a + b. The type discipline is a compile-time concept here. It becomes real in C where ADD_I32 is a 32-bit add and ADD_I64 is a 64-bit add.

8. What this pass deliberately doesn't do
No I8, I16. Defer. Same shape when added.
No unsigned types. Defer.
No comparison opcodes yet. Typed comparisons (EQ_I64, LT_F64, etc.) follow the same pattern but are independent; adding them now would double the opcode count. Do later.
No logical ops (and, or, not for bools) — not in source language anyway.
No overflow checking. I32 ADD_I32 in Python just adds Python ints (unbounded). Real-C behavior would wrap or trap. Defer to C port.
No constant folding for conversions. The Python VM runs CVT ops even when both operands are literals. A later optimization pass can fold CVT_I64_F64 (const 5) into a literal 5.0 at compile time.
9. Example — how the compiled output will look
Source:


set a to 5              # I64
set b to 3.14           # F64
set sum to a + b        # mixed: convert a to F64, then ADD_F64 → F64
print sum

set x to 10 as i32      # explicit i32
set y to 3 as i32
set z to x + y          # both i32, ADD_I32 → I32
print z

set w to x / y          # i32 / i32 → F64 (division rule)
print w
Expected bytecode shape (compact):


; set a to 5
LOAD      r0, <#5:I64>
STORE     r0, <a>                  (a: I64)

; set b to 3.14
LOAD      r0, <#3.14:F64>
STORE     r0, <b>                  (b: F64)

; set sum to a + b
LOAD          r1, <a>              ; I64
LOAD          r2, <b>              ; F64
CVT_I64_F64   r1, r1               ; a promoted to F64
ADD_F64       r0, r1, r2
STORE         r0, <sum>            (sum: F64)

; set x to 10 as i32
LOAD          r0, <#10:I64>
CVT_I64_I32   r0, r0               ; narrow to I32
STORE         r0, <x>              (x: I32)

; set z to x + y
LOAD          r1, <x>              ; I32
LOAD          r2, <y>              ; I32
ADD_I32       r0, r1, r2           ; no conversion needed
STORE         r0, <z>              (z: I32)

; set w to x / y  (division → F64)
LOAD          r1, <x>              ; I32
LOAD          r2, <y>              ; I32
CVT_I32_F64   r1, r1
CVT_I32_F64   r2, r2
DIV_F64       r0, r1, r2
STORE         r0, <w>              (w: F64)
10. Summary of the changes
File	Adds	Changes
parser.py	—	parse_set accepts optional as <type>. SetStmt gets annotated_type field.
bytecode.py	TypeCode.I32, F32. 14 arithmetic opcodes, 12 conversion opcodes.	Remove old ADD, MUL (replaced by typed variants).
compiler.py	compile_binop, promote, emit_convert, typeref_to_code. Matrix index code uses _I64 variants.	compile_set consults annotated_type. compile_expr_into handles BinaryOp.
vm.py	Dispatch for 14 + 12 = 26 new opcodes.	—
Every type has its own opcode at the bytecode level — mechanical to translate to native later. Compiler inserts conversions explicitly; the VM never does implicit coercion.

Want me to implement this? The parser change is ~5 lines, the opcode additions are boilerplate, and the compiler logic is a clean ~80 lines. Total maybe 200 new lines spread across four files