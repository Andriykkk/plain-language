from lexer import tokenize
from parser import Parser
from compiler import compile_program
from vm import execute
from bytecode import dump_module


def run(source: str, show_bytecode: bool = False) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    module = compile_program(stmts)
    if show_bytecode:
        print(dump_module(module))
        print("=== output ===")
    execute(module)


if __name__ == "__main__":
    demo = '''# ---- default types from literals ----
set a to 5                 # I64 (integer literal)
set b to 3.14              # F64 (decimal literal)
set sum to a + b           # mixed: a converted to F64, ADD_F64 → F64
print sum                  # 8.14

# ---- explicit type annotations ----
set x to 10 as i32         # narrow I64 → I32
set y to 3 as i32
set z to x + y             # both I32 → ADD_I32 → I32
print z                    # 13

# ---- integer division becomes float ----
set w to x / y             # I32/I32: both promoted to F64, DIV_F64 → F64
print w                    # 3.333...

# ---- float widths ----
set fa to 1.5 as f32
set fb to 2.5 as f32
set fsum to fa + fb        # both F32 → ADD_F32 → F32
print fsum                 # 4.0

# ---- mixing int sizes ----
set big to 1000000 as i64
set small to 42 as i32
set both to big + small    # I32 promoted to I64, ADD_I64 → I64
print both                 # 1000042
'''
    run(demo, show_bytecode=True)
