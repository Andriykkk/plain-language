The full flow — bytecode → CPU-like execution
Here's the complete picture, end to end.

The four storage areas
Before anything runs, the VM sets up:

Area	Holds	Lifetime	Access
Constants pool	literals known at compile time (numbers, strings, record layouts)	program lifetime	LOADK r, k_idx
Stack	call frames — params, locals, temps of active functions	per call	LOAD.T r, [BP + off] / STORE.T
Heap	dynamic objects — lists, records, maps, matrices, strings	GC-managed	ALLOC, then LOAD.T / STORE.T via pointer
Registers	short-lived scratch during computation	per instruction sequence	direct operand
Constants don't move. Stack grows and shrinks as functions call and return. Heap grows as you allocate. Registers are shared, caller-saved — think of them as the CPU's working memory.

What the compiler does (analysis phase)
One walk over the AST produces a complete Module:


Module
├── functions: {name → Function}
│   └── each Function knows:
│       ├── frame_size      (how many bytes of stack per call)
│       ├── param_layout    (each param's offset from BP)
│       ├── local_layout    (each local's offset from BP)
│       ├── instructions    (the bytecode)
│       └── constants_idx   (references into module's pool)
├── records: {name → RecordLayout}
│   └── each RecordLayout knows:
│       ├── size (total bytes)
│       └── fields: [(name, type, offset)]
└── constants: [value, value, ...]    (interned literals)
Everything static is resolved at compile time: record field offsets, function frame sizes, which slot holds x, which constant index is "hello". The bytecode is then just "memory shuffling with typed ops."

Runtime startup

1. VM allocates stack array: stack[1 MB]     — fixed
2. VM allocates heap:         heap (grows)   — bump allocator or managed
3. BP = 0, SP = 0
4. Main function setup: SP += main.frame_size
5. Begin executing main's first instruction
The execution loop

while IP < current_function.instructions.count:
    instr = current_function.instructions[IP]
    dispatch(instr)   # one of: LOAD, STORE, ADD, JMP, CALL, RET, ALLOC, ...
    IP += 1
No tree-walking, no recursion through AST nodes. Just fetch-decode-execute.

A concrete end-to-end walkthrough
Source:


define record Point
    x as f64
    y as f64
end

define function make_point
    input a as f64
    input b as f64
    output as Point

    set p to new Point
    set p.x to a
    set p.y to b
    return p
end

set pt to call make_point with 3.14 and 2.71
print pt.x
After compilation

Module.records:
  Point: { fields: [(x, F64, off=0), (y, F64, off=8)], size: 16 }

Module.constants: [3.14, 2.71]

Module.functions.make_point:
  frame_size = 24   (param a at BP+0, param b at BP+8, local p at BP+16)
  code:
    ALLOC        r0, 16              ; r0 = heap ptr to new Point (16 bytes)
    STORE.PTR    r0, [BP+16]          ; p = r0
    LOAD.F64     r1, [BP+0]           ; r1 = a
    STORE.F64    r1, r0, #0           ; heap[p + 0] = a
    LOAD.F64     r1, [BP+8]           ; r1 = b
    STORE.F64    r1, r0, #8           ; heap[p + 8] = b
    LOAD.PTR     r0, [BP+16]          ; r0 = p (for return)
    RET          r0

Module.functions.main:
  frame_size = 8   (local pt at BP+0)
  code:
    LOADK        r0, const_idx(3.14)
    STORE.F64    r0, [BP+8]            ; arg 0 area (past main's frame)
    LOADK        r0, const_idx(2.71)
    STORE.F64    r0, [BP+16]           ; arg 1 area
    CALL         #make_point, arg_size=16, frame_size=24
    STORE.PTR    r0, [BP+0]            ; pt = returned pointer
    LOAD.PTR     r0, [BP+0]            ; reload pt
    LOAD.F64     r1, r0, #0            ; r1 = pt.x
    PRINT        r1
    RETN
As the program runs
Startup:


stack: [_][_][_][_][_][_][_][_]  (main's frame, 8 bytes, pt uninitialized)
        ^BP=0                   ^SP=8
heap:  (empty)
regs:  []
Main loads 3.14, 2.71 into arg slots:


stack: [pt=_][arg0=3.14][arg1=2.71][_]...
        ^BP=0           ^           ^
                                    SP=24
CALL make_point transitions:


(control stack: saved_IP, saved_BP=0 pushed)
BP is moved to where args are (old SP before CALL).
New BP = 8, SP = 8 + 24 = 32.

stack: [pt=_][a=3.14][b=2.71][p=_][_]...
                ^BP=8                   ^SP=32
a, b, p are all make_point's locals now. They share the same stack array but live at BP+0, BP+8, BP+16 relative to the new BP.

ALLOC r0, 16 — heap grows:


heap:  [____16_bytes____]
        ^h0
regs:  r0 = h0   (pointer into heap)
STORE.PTR r0, [BP+16] — writes the pointer into p:


stack: [pt=_][a=3.14][b=2.71][p=h0][_]...
LOAD.F64 r1, [BP+0] — reads a from stack into register:


regs: r0 = h0, r1 = 3.14
STORE.F64 r1, r0, #0 — writes 3.14 to heap at [p + 0]:


heap: [3.14][____8_bytes____]
       ^h0
Similar for y, then RET r0 — returns the pointer:


(control stack: pop saved_BP → BP=0, saved_IP → IP=after CALL)
SP = 8  (main's frame restored)
r0 still holds h0
Main's STORE.PTR r0, [BP+0] — stores the pointer into pt:


stack: [pt=h0][_][_][_]...
The stack frame for make_point is gone (SP collapsed). But the heap data survives — the pointer (h0) is still in main's pt. The heap is independent of the call stack.

LOAD.F64 r1, r0, #0 — reads heap[pt+0] = 3.14.
PRINT r1 — outputs 3.14.

What each "store" type does
Op	Reads from	Writes to	Offset is
LOAD.T r, [BP+off]	stack	register	immediate
STORE.T r, [BP+off]	register	stack	immediate
LOAD.T r, r_base, off	heap (via pointer)	register	immediate offset into struct
STORE.T r, r_base, off	register	heap (via pointer)	immediate offset into struct
LOAD.T r, r_base, r_idx	heap (dynamic)	register	from register (for arrays)
The distinction: stack loads use [BP + offset]. Heap loads use [pointer + offset]. Different mode in the instruction, same mechanism underneath.

Dynamic things — lists, strings, maps
These live on the heap with an additional runtime header. For a list of i64:


heap at list_ptr:
  [length: i64] [capacity: i64] [data_ptr: ptr]
                                      │
                                      ▼
                                   heap[data_start..]:
                                     [elem 0: i64][elem 1: i64]...
append 10 to xs:


LOAD.PTR   r0, [BP+off_of_xs]      ; r0 = list header pointer
APPEND     r0, r_value             ; built-in: grows if needed, writes elem,
                                    ; increments length
APPEND is a high-level op that expands into several load/store/compare/alloc sequences. Could be a single opcode in v1 (simpler VM) or lowered to primitives later.

Summary of the flow

Source code
    │
    ▼
Lexer + Parser  →  AST
    │
    ▼
Compiler (full analysis):
  ├── Pass 1: collect functions and record types
  ├── Pass 2: assign record-field offsets, compute record sizes
  ├── Pass 3: for each function, assign stack offsets for params/locals
  ├── Pass 4: emit typed RISC instructions
  └── Produce Module { functions, records, constants }
    │
    ▼
VM ready to run
    │
    ▼
Startup:
  allocate stack (1 MB), empty heap, zero registers
  set up main's frame: BP=0, SP=main.frame_size
    │
    ▼
Execute loop (fetch-decode-execute):
  ├── arithmetic       → registers
  ├── LOAD / STORE      → moves between stack/heap and registers
  ├── ALLOC             → extends heap, returns pointer to register
  ├── CALL              → pushes frame, transfers control
  ├── RET               → pops frame, restores control
  └── JMP / JMPF / JMPT → within-function control flow
    │
    ▼
Program ends when main RETs
(unfreed heap memory either lives until exit or is collected by GC)
Why this is the right model
Matches real CPU semantics exactly. BP/SP/registers + RAM. Nothing magic.
Compile-time layout means no runtime offset computation for records — offsets are immediates baked into instructions.
Heap / stack separation matches how all real systems work. Fast frame allocation (just bump SP), slower but flexible heap for growable things.
Native codegen is a direct translation. Each bytecode op → 1–3 native instructions. Zero runtime-dispatch cost after AOT.
Simple to implement. No graph-coloring register allocator in v1 (registers are scratch; compiler uses them naïvely). Layout is greedy per function.
This is the design. The compiler's job is to lay out everything statically — every offset, every slot, every size — and emit the instruction stream that shuffles values through registers, stack, and heap exactly as a C compiler at -O0 would. The VM's job is to execute that stream as fast as possible, ideally with a tight dispatch loop.

Next step when you're ready: commit to the opcode list for stage 1 (arithmetic + PRINT + RET + stack LOAD/STORE), define the Instruction, Function, Module types, then build the compiler and VM for that subset. The first working program will be set x to 2 + 3; print x — and once that runs end-to-end, extending to everything else is mechanical, not conceptual.