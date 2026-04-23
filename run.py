from lexer import tokenize
from parser import Parser
from evaluator import execute_program
from compiler import compile_program
from vm import execute


def run(source: str) -> None:
    """Reference interpreter — tree-walking, dynamic. Supports the full language."""
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


def run_bytecode(source: str) -> None:
    """Bytecode compiler + SSA VM."""
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    module = compile_program(stmts)
    execute(module)


if __name__ == "__main__":
    demo = '''# full feature demo on the bytecode VM

# ---- types and arithmetic ----
set x to 5
set y to 3
print x + y * 2                       # 11
print 10 / 4                          # 2.5 (float)

# int+float promotion
set pi to 3.14
print x + pi                          # 8.14

# ---- comparisons and branches ----
if x + y is greater than 7
    print "big sum"
else
    print "small sum"
end

# ---- loops ----
set total to 0
repeat for i from 1 to 10
    add i to total
end
print "1..10 sum" and total           # 55

set n to 1
repeat while n is less than 50
    multiply n by 2
end
print "doubled" and n                 # 64

repeat 3 times
    print "hi"
end

# ---- stop / skip ----
set count to 0
repeat 10 times
    add 1 to count
    if count is equal to 3
        skip
    end
    if count is at least 7
        stop
    end
    print "count" and count
end

# ---- functions ----
define function fact
    input n as integer
    output as integer

    if n is at most 1
        return 1
    end
    return n * (call fact with (n - 1))
end

print "5! =" and call fact with 5      # 120

define function sum_to
    input n as integer
    output as integer

    set s to 0
    repeat for i from 0 to n - 1
        add i to s
    end
    return s
end

print "sum 0..9 =" and call sum_to with 10  # 45

# ---- records ----
define record Person
    name as text
    age as integer
end

set alice to new Person
set alice.name to "Alice"
set alice.age to 30
print alice.name and "is" and alice.age

# ---- lists ----
set xs to empty list of integer
append 10 to xs
append 20 to xs
append 30 to xs
print "length" and length of xs
print "first" and xs[0]

set sum to 0
repeat for each v in xs
    add v to sum
end
print "sum of list" and sum

# ---- maps ----
set ages to empty map of text to integer
set ages["Alice"] to 30
set ages["Bob"] to 25
print ages["Alice"]
print ages["Bob"]

# ---- matrices ----
set g to empty matrix 3 by 3 of integer
repeat for i from 0 to 2
    repeat for j from 0 to 2
        set g[i, j] to i * 3 + j
    end
end
print g
print "rows" and rows of g
print "cols" and columns of g
print "total cells" and length of g
'''
    run_bytecode(demo)
