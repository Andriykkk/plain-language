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
    demo = '''set s to "hello"
print s                    # hello

# Assigning a single-character string: compiler converts "H" → 72
set s[0] to "H"
print s                    # Hello

# Assigning a number directly: stored as-is (33 = '!')
set s[4] to 33
print s                    # Hell!

# Assigning an i8 value from elsewhere (another char in the same text)
set s[1] to s[4]           # s[1] = 33 ('!')
print s                    # H!ll!

# The individual slots are still just numbers
print s[0]                 # 72
print s[1]                 # 33
'''
    run(demo)
