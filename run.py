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
    demo = '''# simple if / else
define function calculate_total
    set total to 5
    return total
end
set x to call calculate_total
print x
'''
    run(demo)
