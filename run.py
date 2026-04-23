from lexer import tokenize
from parser import Parser
from evaluator import execute_program


def run(source: str) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


if __name__ == "__main__":
    demo = '''# demo
define function square
    input x as number
    output as number

    return x times x
end function

define function sum_to
    input n as number
    output as number

    set total to 0
    repeat for i from 1 to n
        add i to total
    end
    return total
end

define function factorial
    input n as number
    output as number

    if n is at most 1
        return 1
    end
    return n times call factorial with (n minus 1)
end

print "square of 7 is" and call square with 7
print "sum 1..10 is" and call sum_to with 10
print "5! is" and call factorial with 5
'''
    run(demo)
