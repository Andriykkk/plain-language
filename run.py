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
    demo = '''# dynamic list — grows with append
set xs to empty list of integer
append 10 to xs
append 20 to xs
append 30 to xs
print xs[0]
print length of xs

# static 2x3 matrix — one contiguous block, shape known at compile time
set m to empty matrix 2 by 3 of integer
set m[0, 0] to 100
set m[0, 1] to 200
set m[0, 2] to 300
set m[1, 0] to 400
set m[1, 1] to 500
set m[1, 2] to 600

print m[0, 1]
print m[1, 2]
print rows of m
print columns of m
print length of m
'''
    run(demo, show_bytecode=True)
