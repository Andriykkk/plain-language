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
set x to 5
if x is greater than 0
    print "positive"
else
    print "non-positive"
end

# if with no else
set y to 10
if y is equal to 10
    print "ten"
end

# else-if chain
set grade to 72
if grade is at least 90
    print "A"
else if grade is at least 80
    print "B"
else if grade is at least 70
    print "C"
else
    print "F"
end

# nested ifs
set a to 5
set b to 3
if a is greater than 0
    if b is greater than 0
        print "both positive"
    else
        print "only a positive"
    end
end

# comparison with mixed types — a is i64, 4.5 is f64; compiler promotes
if a is greater than 4.5
    print "greater than 4.5"
end

# comparison on text
set s to "hello"
set t to "hello"
if s is equal to t
    print "strings match"
end
'''
    run(demo)
