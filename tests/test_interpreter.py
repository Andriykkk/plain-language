import io
import unittest
from contextlib import redirect_stdout

# The evaluator is gone — `run()` now goes through the bytecode path.
# Tests catch whichever of the three error types the new pipeline raises.
from compiler import CompileError
from parser import ParseError
from vm import VMError
from run import run

# Backwards-compatible name: tests that used to catch RunError now catch
# either a compile-time or a runtime error from the bytecode pipeline.
RunError = (CompileError, VMError)


def run_capture(source: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        run(source)
    return buf.getvalue()


class TestLiterals(unittest.TestCase):
    def test_set_and_print_number(self):
        self.assertEqual(run_capture("set x to 5\nprint x\n"), "5\n")

    def test_print_string(self):
        self.assertEqual(run_capture('set s to "hello"\nprint s\n'), "hello\n")

    def test_string_escapes(self):
        self.assertEqual(run_capture('print "a\\nb"\n'), "a\nb\n")

    def test_booleans_and_none(self):
        self.assertEqual(
            run_capture("print true, false, none\n"),
            "True False None\n",
        )

    def test_float_literal(self):
        self.assertEqual(run_capture("set x to 3.14\nprint x\n"), "3.14\n")


class TestSymbolicOperators(unittest.TestCase):
    def test_plus_symbol(self):
        self.assertEqual(run_capture("set x to 2 + 3\nprint x\n"), "5\n")

    def test_minus_symbol(self):
        self.assertEqual(run_capture("set x to 10 - 4\nprint x\n"), "6\n")

    def test_star_symbol(self):
        self.assertEqual(run_capture("set x to 6 * 7\nprint x\n"), "42\n")

    def test_slash_symbol(self):
        self.assertEqual(run_capture("set x to 10 / 4\nprint x\n"), "2.5\n")

    def test_precedence_star_before_plus(self):
        self.assertEqual(run_capture("set x to 2 + 3 * 4\nprint x\n"), "14\n")

    def test_parens_override_precedence(self):
        self.assertEqual(run_capture("set x to (2 + 3) * 4\nprint x\n"), "20\n")

    def test_unary_minus_literal(self):
        self.assertEqual(run_capture("set x to -5\nprint x\n"), "-5\n")

    def test_unary_minus_expression(self):
        self.assertEqual(run_capture("set x to -(2 + 3)\nprint x\n"), "-5\n")

    def test_unary_minus_variable(self):
        self.assertEqual(run_capture("set a to 7\nset b to -a\nprint b\n"), "-7\n")

    def test_mix_symbols_and_words(self):
        # symbols and words produce the same AST; this should compute 2 + 12 = 14
        self.assertEqual(run_capture("set x to 2 + 3 times 4\nprint x\n"), "14\n")
        self.assertEqual(run_capture("set x to 2 plus 3 * 4\nprint x\n"), "14\n")

    def test_symbol_in_loop_bounds(self):
        src = """set total to 0
repeat for i from 0 to 10 - 1
    add i to total
end
print total
"""
        self.assertEqual(run_capture(src), "45\n")

    def test_symbol_in_index_math(self):
        src = """set xs to empty list of number
append 10 to xs
append 20 to xs
append 30 to xs
print xs[length of xs - 1]
"""
        self.assertEqual(run_capture(src), "30\n")

    def test_times_word_still_disabled_in_repeat_count(self):
        # 'repeat N times' — the word 'times' remains the loop marker, not multiplication.
        # Using '*' in the count is fine because the symbol doesn't collide.
        src = """set n to 0
repeat 2 * 3 times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "6\n")


class TestArithmetic(unittest.TestCase):
    def test_precedence_times_before_plus(self):
        self.assertEqual(run_capture("set x to 2 plus 3 times 4\nprint x\n"), "14\n")

    def test_parentheses_override_precedence(self):
        self.assertEqual(run_capture("set x to (2 plus 3) times 4\nprint x\n"), "20\n")

    def test_minus_and_plus_chain(self):
        self.assertEqual(run_capture("set x to 10 minus 3 plus 1\nprint x\n"), "8\n")

    def test_divided_by(self):
        self.assertEqual(run_capture("set x to 10 divided by 4\nprint x\n"), "2.5\n")

    def test_multiline_expression_inside_parens(self):
        src = """set x to (
    2 plus 3
) times 4
print x
"""
        self.assertEqual(run_capture(src), "20\n")


class TestStatementOps(unittest.TestCase):
    def test_add_to(self):
        self.assertEqual(run_capture("set x to 10\nadd 5 to x\nprint x\n"), "15\n")

    def test_subtract_from(self):
        self.assertEqual(run_capture("set x to 10\nsubtract 3 from x\nprint x\n"), "7\n")

    def test_multiply_by(self):
        self.assertEqual(run_capture("set x to 4\nmultiply x by 3\nprint x\n"), "12\n")

    def test_divide_by(self):
        # `x` is declared i64; the float result of `/` is narrowed back to i64
        # on assignment (truncation toward zero). To keep the float, use an
        # f64 variable.
        self.assertEqual(run_capture("set x to 10\ndivide x by 4\nprint x\n"), "2\n")

    def test_all_four_in_sequence(self):
        src = """set x to 10
add 5 to x
subtract 3 from x
multiply x by 2
divide x by 4
print x
"""
        self.assertEqual(run_capture(src), "6\n")


class TestPrint(unittest.TestCase):
    def test_print_joins_with_and(self):
        self.assertEqual(
            run_capture('set x to 42\nprint "answer is", x\n'),
            "answer is 42\n",
        )

    def test_print_expression(self):
        self.assertEqual(run_capture("print 2 plus 3\n"), "5\n")


class TestProgramStructure(unittest.TestCase):
    def test_comments_and_blank_lines_ignored(self):
        src = """# leading comment

set x to 1
# between
set y to 2

# before print
print x plus y
"""
        self.assertEqual(run_capture(src), "3\n")


class TestComparisons(unittest.TestCase):
    def test_equal_to(self):
        self.assertEqual(run_capture("print 5 is equal to 5\n"), "True\n")
        self.assertEqual(run_capture("print 5 is equal to 6\n"), "False\n")

    def test_not_equal_to(self):
        self.assertEqual(run_capture("print 5 is not equal to 6\n"), "True\n")
        self.assertEqual(run_capture("print 5 is not equal to 5\n"), "False\n")

    def test_greater_than(self):
        self.assertEqual(run_capture("print 5 is greater than 3\n"), "True\n")
        self.assertEqual(run_capture("print 3 is greater than 5\n"), "False\n")

    def test_less_than(self):
        self.assertEqual(run_capture("print 3 is less than 5\n"), "True\n")

    def test_at_least(self):
        self.assertEqual(run_capture("print 5 is at least 5\n"), "True\n")
        self.assertEqual(run_capture("print 4 is at least 5\n"), "False\n")

    def test_at_most(self):
        self.assertEqual(run_capture("print 5 is at most 5\n"), "True\n")
        self.assertEqual(run_capture("print 6 is at most 5\n"), "False\n")

    def test_string_equality(self):
        self.assertEqual(run_capture('print "hi" is equal to "hi"\n'), "True\n")

    def test_comparison_with_expression(self):
        self.assertEqual(run_capture("print 2 plus 3 is equal to 5\n"), "True\n")


class TestBranches(unittest.TestCase):
    def test_if_true(self):
        src = """if 1 is less than 2
    print "yes"
end
"""
        self.assertEqual(run_capture(src), "yes\n")

    def test_if_false_no_else(self):
        src = """if 1 is greater than 2
    print "no"
end
"""
        self.assertEqual(run_capture(src), "")

    def test_if_else(self):
        src = """if 1 is greater than 2
    print "no"
else
    print "yes"
end
"""
        self.assertEqual(run_capture(src), "yes\n")

    def test_else_if_chain(self):
        src = """set x to 15
if x is greater than 100
    print "huge"
else if x is greater than 10
    print "medium"
else
    print "small"
end
"""
        self.assertEqual(run_capture(src), "medium\n")

    def test_nested_if(self):
        src = """set x to 5
if x is greater than 0
    if x is less than 10
        print "in range"
    end
end
"""
        self.assertEqual(run_capture(src), "in range\n")


class TestLoops(unittest.TestCase):
    def test_repeat_times(self):
        src = """set n to 0
repeat 5 times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "5\n")

    def test_repeat_times_with_plus_in_count(self):
        src = """set n to 0
repeat 2 plus 3 times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "5\n")

    def test_repeat_times_with_parens(self):
        src = """set n to 0
repeat (2 times 3) times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "6\n")

    def test_repeat_range_inclusive(self):
        src = """set total to 0
repeat for i from 1 to 5
    add i to total
end
print total
"""
        self.assertEqual(run_capture(src), "15\n")

    def test_repeat_while(self):
        src = """set n to 1
repeat while n is less than 10
    multiply n by 2
end
print n
"""
        self.assertEqual(run_capture(src), "16\n")

    def test_repeat_for_each_over_string(self):
        src = """set count to 0
repeat for each c in "abc"
    add 1 to count
end
print count
"""
        self.assertEqual(run_capture(src), "3\n")

    def test_stop(self):
        src = """set n to 0
repeat 10 times
    add 1 to n
    if n is equal to 3
        stop
    end
end
print n
"""
        self.assertEqual(run_capture(src), "3\n")

    def test_skip(self):
        src = """set count to 0
repeat for i from 1 to 5
    if i is equal to 3
        skip
    end
    add 1 to count
end
print count
"""
        self.assertEqual(run_capture(src), "4\n")


class TestBlockEnd(unittest.TestCase):
    def test_bare_end_closes_if(self):
        self.assertEqual(
            run_capture("if true\n    print 1\nend\n"),
            "1\n",
        )

    def test_end_if_closes_if(self):
        self.assertEqual(
            run_capture("if true\n    print 1\nend if\n"),
            "1\n",
        )

    def test_bare_end_closes_repeat(self):
        self.assertEqual(
            run_capture("repeat 1 times\n    print 1\nend\n"),
            "1\n",
        )

    def test_end_repeat_closes_repeat(self):
        self.assertEqual(
            run_capture("repeat 1 times\n    print 1\nend repeat\n"),
            "1\n",
        )

    def test_mismatched_end_repeat_on_if_errors(self):
        with self.assertRaises(ParseError):
            run_capture("if true\n    print 1\nend repeat\n")

    def test_mismatched_end_if_on_repeat_errors(self):
        with self.assertRaises(ParseError):
            run_capture("repeat 1 times\n    print 1\nend if\n")


class TestFunctions(unittest.TestCase):
    def test_no_args_no_return(self):
        src = """define function hello
    print "hi"
end

call hello
"""
        self.assertEqual(run_capture(src), "hi\n")

    def test_one_arg_with_return(self):
        src = """define function double
    input x as number
    output as number

    return x times 2
end

print call double with 21
"""
        self.assertEqual(run_capture(src), "42.0\n")

    def test_multiple_args(self):
        src = """define function add3
    input a as number
    input b as number
    input c as number
    output as number

    return a plus b plus c
end

print call add3 with 1, 2, 3
"""
        self.assertEqual(run_capture(src), "6.0\n")

    def test_call_as_statement_for_side_effects(self):
        src = """define function greet
    input name as text

    print "hello", name
end

call greet with "world"
"""
        self.assertEqual(run_capture(src), "hello world\n")

    def test_recursion(self):
        src = """define function fact
    input n as i64
    output as i64

    if n is at most 1
        return 1
    end
    return n times call fact with (n minus 1)
end

print call fact with 5
"""
        self.assertEqual(run_capture(src), "120\n")

    def test_function_calling_function(self):
        src = """define function square
    input x as number
    output as number

    return x times x
end

define function square_plus_one
    input x as number
    output as number

    return (call square with x) plus 1
end

print call square_plus_one with 4
"""
        self.assertEqual(run_capture(src), "17.0\n")

    def test_bare_return_gives_none(self):
        src = """define function nothing
    return
end

set x to call nothing
print x
"""
        self.assertEqual(run_capture(src), "None\n")

    def test_falls_off_end_gives_none(self):
        src = """define function noop
    set _ to 1
end

set x to call noop
print x
"""
        self.assertEqual(run_capture(src), "None\n")

    def test_function_local_scope_isolated(self):
        # A function's local variables should not bleed into the caller.
        src = """define function scoped
    set local_var to 999
end

call scoped
set local_var to 1
print local_var
"""
        self.assertEqual(run_capture(src), "1\n")

    def test_function_sees_top_level_functions_not_caller_locals(self):
        # Functions must see other functions (top-level) but NOT caller's local variables.
        src = """define function uses_helper
    return call helper
end

define function helper
    output as number

    return 42
end

print call uses_helper
"""
        self.assertEqual(run_capture(src), "42.0\n")

    def test_return_value_in_expression(self):
        src = """define function five
    output as i64

    return 5
end

print (call five) plus 10
"""
        self.assertEqual(run_capture(src), "15\n")

    def test_end_function_closes_function(self):
        src = """define function f
    return 1
end function

print call f
"""
        self.assertEqual(run_capture(src), "1\n")

    def test_mismatched_end_if_on_function_errors(self):
        src = """define function f
    return 1
end if
"""
        with self.assertRaises(ParseError):
            run_capture(src)


class TestFunctionErrors(unittest.TestCase):
    def test_undefined_function(self):
        with self.assertRaises(RunError):
            run_capture("call missing\n")

    def test_wrong_arg_count(self):
        src = """define function f
    input x as number
    output as number

    return x
end

print call f with 1, 2
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_return_outside_function(self):
        with self.assertRaises(RunError):
            run_capture("return 5\n")

    def test_calling_a_non_function(self):
        src = """set x to 5
print call x
"""
        with self.assertRaises(RunError):
            run_capture(src)


class TestRecords(unittest.TestCase):
    def test_define_and_set_fields(self):
        src = """define record Person
    name as text
    age as i64
end

set u to new Person
set u.name to "Alice"
set u.age to 30
print u.name, u.age
"""
        self.assertEqual(run_capture(src), "Alice 30\n")

    def test_define_and_set_fields_float(self):
        src = """define record Person
    name as text
    age as float
end

set u to new Person
set u.name to "Alice"
set u.age to 30
print u.name, u.age
"""
        self.assertEqual(run_capture(src), "Alice 30.0\n")

    def test_default_field_values(self):
        # number defaults to 0, text to "", list to [], map to {}
        src = """define record Box
    count as number
    label as text
    items as list of number
end

set b to new Box
print b.count
print b.label is equal to ""
print length of b.items
"""
        self.assertEqual(run_capture(src), "0.0\nTrue\n0\n")

    def test_add_to_field(self):
        src = """define record Account
    balance as number
end

set a to new Account
set a.balance to 50
add 25 to a.balance
print a.balance
"""
        self.assertEqual(run_capture(src), "75.0\n")

    def test_multiply_field(self):
        src = """define record Rect
    w as i64
    h as i64
end

set r to new Rect
set r.w to 4
set r.h to 5
multiply r.w by r.h
print r.w
"""
        self.assertEqual(run_capture(src), "20\n")

    def test_multiply_field_float(self):
        src = """define record Rect
    w as float
    h as float
end

set r to new Rect
set r.w to 4
set r.h to 5
multiply r.w by r.h
print r.w
"""
        self.assertEqual(run_capture(src), "20.0\n")

    def test_nested_field_access(self):
        src = """define record Inner
    value as i64
end

define record Outer
    inner as Inner
end

set o to new Outer
set o.inner.value to 42
print o.inner.value
"""
        self.assertEqual(run_capture(src), "42\n")

    def test_end_record_closes_record(self):
        src = """define record R
    x as i64
end record

set r to new R
print r.x
"""
        self.assertEqual(run_capture(src), "0\n")

    def test_end_record_closes_record_float(self):
        src = """define record R
    x as float
end record

set r to new R
print r.x
"""
        self.assertEqual(run_capture(src), "0.0\n")

    def test_mismatched_end_if_on_record_errors(self):
        src = """define record R
    x as number
end if
"""
        with self.assertRaises(ParseError):
            run_capture(src)

    def test_field_access_on_non_record_errors(self):
        with self.assertRaises(RunError):
            run_capture("set x to 5\nprint x.name\n")

    def test_unknown_field_errors(self):
        src = """define record P
    name as text
end

set p to new P
print p.missing
"""
        with self.assertRaises(RunError):
            run_capture(src)


class TestLists(unittest.TestCase):
    def test_empty_list_and_append(self):
        src = """set xs to empty list of number
append 10 to xs
append 20 to xs
print length of xs
print xs[0]
print xs[1]
"""
        self.assertEqual(run_capture(src), "2\n10\n20\n")

    def test_list_is_zero_indexed(self):
        src = """set xs to empty list of number
append 100 to xs
append 200 to xs
append 300 to xs
print xs[0]
print xs[2]
"""
        self.assertEqual(run_capture(src), "100\n300\n")

    def test_set_list_element(self):
        src = """set xs to empty list of number
append 1 to xs
append 2 to xs
append 3 to xs
set xs[1] to 99
print xs[1]
"""
        self.assertEqual(run_capture(src), "99.0\n")

    def test_add_to_list_element(self):
        src = """set xs to empty list of number
append 10 to xs
append 20 to xs
add 5 to xs[0]
print xs[0]
"""
        self.assertEqual(run_capture(src), "15.0\n")

    def test_iterate_list(self):
        src = """set xs to empty list of number
append 1 to xs
append 2 to xs
append 3 to xs

set total to 0
repeat for each v in xs
    add v to total
end
print total
"""
        self.assertEqual(run_capture(src), "6\n")

    def test_index_out_of_range_errors(self):
        src = """set xs to empty list of number
append 1 to xs
print xs[5]
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_append_to_non_list_errors(self):
        with self.assertRaises(RunError):
            run_capture("set x to 5\nappend 1 to x\n")


class TestMaps(unittest.TestCase):
    def test_empty_map_and_assign(self):
        src = """set ages to empty map of text to i64
set ages["Alice"] to 30
set ages["Bob"] to 25
print ages["Alice"]
print ages["Bob"]
"""
        self.assertEqual(run_capture(src), "30\n25\n")

    def test_map_length(self):
        src = """set m to empty map of text to i64
set m["a"] to 1
set m["b"] to 2
print length of m
"""
        self.assertEqual(run_capture(src), "2\n")

    def test_missing_key_errors(self):
        src = """set m to empty map of text to i64
print m["missing"]
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_add_to_map_value(self):
        src = """set m to empty map of text to i64
set m["x"] to 10
add 5 to m["x"]
print m["x"]
"""
        self.assertEqual(run_capture(src), "15\n")

    def test_integer_keys(self):
        src = """set m to empty map of i64 to text
set m[1] to "one"
set m[2] to "two"
print m[1]
print m[2]
"""
        self.assertEqual(run_capture(src), "one\ntwo\n")

    def test_float_values(self):
        src = """set m to empty map of text to f64
set m["pi"] to 3.14
set m["e"] to 2.71
print m["pi"]
print m["e"]
"""
        self.assertEqual(run_capture(src), "3.14\n2.71\n")

    def test_bool_values(self):
        src = """set m to empty map of text to bool
set m["yes"] to true
set m["no"] to false
print m["yes"]
print m["no"]
"""
        self.assertEqual(run_capture(src), "True\nFalse\n")

    def test_record_values(self):
        # Map values can be records — the dict stores the heap pointer.
        src = """define record Person
    name as text
    age as i64
end

set people to empty map of text to Person

set p to new Person
set p.name to "Alice"
set p.age to 30
set people["alice"] to p

set q to new Person
set q.name to "Bob"
set q.age to 42
set people["bob"] to q

print length of people
"""
        self.assertEqual(run_capture(src), "2\n")


class TestMatrices(unittest.TestCase):
    def test_2d_matrix_defaults_to_zero(self):
        src = """set g to empty matrix 2 by 3 of number
print g[0, 0]
print g[1, 2]
print length of g
"""
        self.assertEqual(run_capture(src), "0.0\n0.0\n6\n")

    def test_set_and_read_cells(self):
        src = """set g to empty matrix 3 by 3 of number
set g[0, 0] to 10
set g[1, 1] to 20
set g[2, 2] to 30
print g[0, 0]
print g[1, 1]
print g[2, 2]
"""
        self.assertEqual(run_capture(src), "10.0\n20.0\n30.0\n")

    def test_rows_and_columns(self):
        src = """set g to empty matrix 4 by 7 of number
print rows of g
print columns of g
"""
        self.assertEqual(run_capture(src), "4\n7\n")

    def test_add_to_cell(self):
        src = """set g to empty matrix 2 by 2 of number
set g[0, 0] to 5
add 3 to g[0, 0]
print g[0, 0]
"""
        self.assertEqual(run_capture(src), "8.0\n")

    def test_multiply_cell(self):
        src = """set g to empty matrix 2 by 2 of number
set g[1, 0] to 4
multiply g[1, 0] by 3
print g[1, 0]
"""
        self.assertEqual(run_capture(src), "12.0\n")

    def test_3d_matrix(self):
        src = """set cube to empty matrix 2 by 2 by 2 of i64
set cube[0, 0, 0] to 1
set cube[1, 1, 1] to 8
print length of cube
print cube[0, 0, 0]
print cube[1, 1, 1]
"""
        self.assertEqual(run_capture(src), "8\n1\n8\n")

    def test_4d_matrix(self):
        src = """set t to empty matrix 2 by 2 by 2 by 2 of i64
set t[0, 0, 0, 0] to 1
set t[1, 1, 1, 1] to 16
print length of t
print t[0, 0, 0, 0]
print t[1, 1, 1, 1]
"""
        self.assertEqual(run_capture(src), "16\n1\n16\n")

    def test_3d_matrix_default_zeroed(self):
        src = """set cube to empty matrix 2 by 3 by 4 of i64
print cube[0, 0, 0]
print cube[1, 2, 3]
print length of cube
"""
        self.assertEqual(run_capture(src), "0\n0\n24\n")

    def test_rows_columns_read_from_descriptor(self):
        # `rows of m` and `columns of m` should reflect the actual stored
        # shape — that's what the descriptor provides.
        src = """set g to empty matrix 5 by 7 of i64
print rows of g
print columns of g
"""
        self.assertEqual(run_capture(src), "5\n7\n")

    def test_3d_iterate_flat(self):
        src = """set cube to empty matrix 2 by 2 by 2 of i64
set cube[0, 0, 0] to 1
set cube[0, 0, 1] to 2
set cube[0, 1, 0] to 3
set cube[0, 1, 1] to 4
set cube[1, 0, 0] to 5
set cube[1, 0, 1] to 6
set cube[1, 1, 0] to 7
set cube[1, 1, 1] to 8
set total to 0
repeat for each v in cube
    add v to total
end
print total
"""
        self.assertEqual(run_capture(src), "36\n")

    def test_matrix_of_text(self):
        src = """set labels to empty matrix 2 by 2 of text
set labels[0, 0] to "NW"
set labels[1, 1] to "SE"
print labels[0, 0]
print labels[1, 1]
print labels[0, 1]
"""
        # empty text default is ""
        self.assertEqual(run_capture(src), "NW\nSE\n\n")

    def test_nested_iteration_sum(self):
        src = """set g to empty matrix 3 by 3 of number
repeat for i from 0 to 2
    repeat for j from 0 to 2
        set g[i, j] to i plus j
    end
end
set total to 0
repeat for i from 0 to 2
    repeat for j from 0 to 2
        add g[i, j] to total
    end
end
print total
"""
        # Sum of i+j for i in 0..2, j in 0..2 = 3*(0+1+2) + 3*(0+1+2) = 9+9 = 18
        self.assertEqual(run_capture(src), "18\n")

    def test_for_each_iterates_flat(self):
        src = """set g to empty matrix 2 by 2 of number
set g[0, 0] to 1
set g[0, 1] to 2
set g[1, 0] to 3
set g[1, 1] to 4
set total to 0
repeat for each v in g
    add v to total
end
print total
"""
        self.assertEqual(run_capture(src), "10\n")

    def test_wrong_number_of_indices_errors(self):
        src = """set g to empty matrix 2 by 2 of number
print g[0]
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_out_of_range_errors(self):
        src = """set g to empty matrix 2 by 2 of number
print g[2, 0]
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_multi_index_on_list_errors(self):
        src = """set xs to empty list of number
append 1 to xs
print xs[0, 0]
"""
        with self.assertRaises(RunError):
            run_capture(src)

    def test_rows_of_non_matrix_errors(self):
        with self.assertRaises(RunError):
            run_capture("set xs to empty list of number\nprint rows of xs\n")

    def test_matrix_multiply(self):
        # integration test — small 2x2 matrix multiply
        src = """set a to empty matrix 2 by 2 of number
set a[0, 0] to 1
set a[0, 1] to 2
set a[1, 0] to 3
set a[1, 1] to 4

set b to empty matrix 2 by 2 of number
set b[0, 0] to 5
set b[0, 1] to 6
set b[1, 0] to 7
set b[1, 1] to 8

set c to empty matrix 2 by 2 of number
repeat for i from 0 to 1
    repeat for j from 0 to 1
        set sum to 0
        repeat for k from 0 to 1
            add a[i, k] times b[k, j] to sum
        end
        set c[i, j] to sum
    end
end
print c[0, 0]
print c[0, 1]
print c[1, 0]
print c[1, 1]
"""
        # [[1,2],[3,4]] * [[5,6],[7,8]] = [[19,22],[43,50]]
        self.assertEqual(run_capture(src), "19.0\n22.0\n43.0\n50.0\n")


class TestListsOfRecords(unittest.TestCase):
    def test_append_record_and_access_fields(self):
        src = """define record Item
    name as text
    price as number
end

set items to empty list of Item

set a to new Item
set a.name to "apple"
set a.price to 2
append a to items

set b to new Item
set b.name to "bread"
set b.price to 5
append b to items

print items[0].name
print items[1].price

set total to 0
repeat for each it in items
    add it.price to total
end
print total
"""
        self.assertEqual(run_capture(src), "apple\n5.0\n7\n")


class TestBitwise(unittest.TestCase):
    # symbol forms

    def test_bit_and_symbol(self):
        self.assertEqual(run_capture("print 12 & 10\n"), "8\n")

    def test_bit_or_symbol(self):
        self.assertEqual(run_capture("print 12 | 10\n"), "14\n")

    def test_bit_xor_symbol(self):
        self.assertEqual(run_capture("print 12 ^ 10\n"), "6\n")

    def test_bit_not_symbol(self):
        self.assertEqual(run_capture("print ~ 0\n"), "-1\n")

    def test_shift_left_symbol(self):
        self.assertEqual(run_capture("print 1 << 4\n"), "16\n")

    def test_shift_right_symbol(self):
        self.assertEqual(run_capture("print 256 >> 2\n"), "64\n")

    # word forms

    def test_bit_and_word(self):
        self.assertEqual(run_capture("print 12 bit_and 10\n"), "8\n")

    def test_bit_or_word(self):
        self.assertEqual(run_capture("print 12 bit_or 10\n"), "14\n")

    def test_bit_xor_word(self):
        self.assertEqual(run_capture("print 12 xor 10\n"), "6\n")

    def test_bit_not_word(self):
        self.assertEqual(run_capture("print bit_not 0\n"), "-1\n")

    def test_shifted_left_word(self):
        self.assertEqual(run_capture("print 1 shifted left by 4\n"), "16\n")

    def test_shifted_right_word(self):
        self.assertEqual(
            run_capture("print 256 shifted right by 2\n"), "64\n"
        )

    # combined / precedence

    def test_or_xor_and_precedence(self):
        # AND binds tighter than XOR which binds tighter than OR.
        # 12 & 10 = 8; 8 ^ 5 = 13; 13 | 1 = 13.
        self.assertEqual(run_capture("print 12 & 10 ^ 5 | 1\n"), "13\n")

    def test_addition_higher_than_shift(self):
        # `+` binds tighter than `<<` (matches Python).
        # `5 + 1 << 3` = `(5 + 1) << 3` = `6 << 3` = 48.
        self.assertEqual(run_capture("print 5 + 1 << 3\n"), "48\n")

    def test_shift_higher_than_bit_and(self):
        # `<<` binds tighter than `&`.
        # `8 & 1 << 3` = `8 & (1 << 3)` = `8 & 8` = 8.
        self.assertEqual(run_capture("print 8 & 1 << 3\n"), "8\n")

    def test_bit_and_higher_than_bit_or(self):
        # `&` binds tighter than `|`.
        # `1 | 2 & 0` = `1 | (2 & 0)` = `1 | 0` = 1.
        self.assertEqual(run_capture("print 1 | 2 & 0\n"), "1\n")

    # error cases

    def test_bitwise_rejects_float(self):
        with self.assertRaises(RunError):
            run_capture("print 1.5 & 2\n")

    def test_bitwise_rejects_float_with_word_form(self):
        with self.assertRaises(RunError):
            run_capture("set x to 3.14\nprint x bit_and 1\n")

    def test_bit_not_rejects_float(self):
        with self.assertRaises(RunError):
            run_capture("print bit_not 3.14\n")


class TestLogical(unittest.TestCase):
    # Python-style: return the chosen operand when types match.

    def test_and_returns_left_when_falsy(self):
        self.assertEqual(run_capture("print 0 and 5\n"), "0\n")

    def test_and_returns_right_when_left_truthy(self):
        self.assertEqual(run_capture("print 5 and 3\n"), "3\n")

    def test_or_returns_left_when_truthy(self):
        self.assertEqual(run_capture("print 5 or 3\n"), "5\n")

    def test_or_returns_right_when_left_falsy(self):
        self.assertEqual(run_capture("print 0 or 5\n"), "5\n")

    def test_or_returns_falsy_right_if_left_falsy(self):
        self.assertEqual(run_capture("print 0 or 0\n"), "0\n")

    # Word and symbol forms are equivalent.

    def test_double_amp_same_as_and(self):
        self.assertEqual(run_capture("print 5 && 3\n"), "3\n")

    def test_double_pipe_same_as_or(self):
        self.assertEqual(run_capture("print 0 || 5\n"), "5\n")

    def test_bang_same_as_not(self):
        self.assertEqual(run_capture("print !5\n"), "False\n")
        self.assertEqual(run_capture("print !0\n"), "True\n")

    # Default-value idiom on TEXT.

    def test_or_default_text(self):
        src = """set name to ""
print name or "anonymous"
"""
        self.assertEqual(run_capture(src), "anonymous\n")

    def test_or_keeps_non_empty_text(self):
        src = """set name to "Alice"
print name or "anonymous"
"""
        self.assertEqual(run_capture(src), "Alice\n")

    # Mixed types collapse to BOOL via truthiness.

    def test_and_mixed_types_collapses_to_bool(self):
        src = """set xs to empty list of i64
print xs and xs[0]
"""
        self.assertEqual(run_capture(src), "False\n")

    def test_and_mixed_types_truthy_path(self):
        src = """set xs to empty list of i64
append 42 to xs
print xs and xs[0]
"""
        self.assertEqual(run_capture(src), "True\n")

    # Short-circuit: right side not evaluated when left determines.

    def test_short_circuit_and_skips_right_on_falsy_left(self):
        # If 'and' didn't short-circuit, xs[0] on an empty list would throw
        # an out-of-range error. It should compile to a BOOL = False.
        src = """set xs to empty list of i64
print xs and xs[0]
"""
        self.assertEqual(run_capture(src), "False\n")

    def test_short_circuit_or_skips_right_on_truthy_left(self):
        # If `or` didn't short-circuit, xs[100] would crash with an
        # out-of-range error. Same-type operands (both i64) so the result
        # type stays i64 and prints "5", not "True".
        src = """set xs to empty list of i64
print 5 or xs[100]
"""
        self.assertEqual(run_capture(src), "5\n")

    # not / !

    def test_not_true(self):
        self.assertEqual(run_capture("print not true\n"), "False\n")

    def test_not_false(self):
        self.assertEqual(run_capture("print not false\n"), "True\n")

    def test_not_zero_is_true(self):
        self.assertEqual(run_capture("print not 0\n"), "True\n")

    def test_not_nonzero_is_false(self):
        self.assertEqual(run_capture("print not 5\n"), "False\n")

    def test_not_empty_text_is_true(self):
        self.assertEqual(run_capture('print not ""\n'), "True\n")

    def test_not_nonempty_text_is_false(self):
        self.assertEqual(run_capture('print not "x"\n'), "False\n")

    def test_not_empty_list_is_true(self):
        src = """set xs to empty list of i64
print not xs
"""
        self.assertEqual(run_capture(src), "True\n")

    def test_not_nonempty_list_is_false(self):
        src = """set xs to empty list of i64
append 1 to xs
print not xs
"""
        self.assertEqual(run_capture(src), "False\n")

    # if/while accept any-typed conditions via implicit truthiness.

    def test_if_with_int_condition(self):
        src = """set x to 5
if x
    print "truthy"
else
    print "falsy"
end
"""
        self.assertEqual(run_capture(src), "truthy\n")

    def test_if_with_zero_int_is_falsy(self):
        src = """set x to 0
if x
    print "truthy"
else
    print "falsy"
end
"""
        self.assertEqual(run_capture(src), "falsy\n")

    def test_if_with_text_condition(self):
        src = """set s to ""
if s
    print "truthy"
else
    print "falsy"
end
"""
        self.assertEqual(run_capture(src), "falsy\n")

    def test_if_with_logical_chain(self):
        src = """set x to 5
set y to 3
if x is greater than 0 and y is greater than 0
    print "both positive"
end
"""
        self.assertEqual(run_capture(src), "both positive\n")

    def test_while_with_int_condition(self):
        src = """set n to 3
repeat while n
    print n
    subtract 1 from n
end
"""
        self.assertEqual(run_capture(src), "3\n2\n1\n")

    # Compound combinations.

    def test_and_or_precedence(self):
        # AND binds tighter than OR. `0 or 1 and 0` = `0 or (1 and 0)` = `0 or 0` = `0`.
        self.assertEqual(run_capture("print 0 or 1 and 0\n"), "0\n")

    def test_chained_or_returns_first_truthy(self):
        self.assertEqual(run_capture("print 0 or 0 or 7 or 0\n"), "7\n")

    def test_chained_and_returns_first_falsy(self):
        self.assertEqual(run_capture("print 5 and 0 and 7\n"), "0\n")


class TestCast(unittest.TestCase):
    # Postfix `as <type>`

    def test_int_to_bool_truthy(self):
        self.assertEqual(run_capture("print 5 as bool\n"), "True\n")

    def test_int_to_bool_falsy(self):
        self.assertEqual(run_capture("print 0 as bool\n"), "False\n")

    def test_text_to_bool_empty(self):
        self.assertEqual(run_capture('print "" as bool\n'), "False\n")

    def test_text_to_bool_non_empty(self):
        self.assertEqual(run_capture('print "x" as bool\n'), "True\n")

    def test_float_to_int_truncates(self):
        self.assertEqual(run_capture("print 3.7 as i64\n"), "3\n")

    def test_negative_float_to_int_truncates(self):
        self.assertEqual(run_capture("print -3.7 as i64\n"), "-3\n")

    def test_int_to_float(self):
        self.assertEqual(run_capture("print 5 as f64\n"), "5.0\n")

    def test_bool_to_int(self):
        self.assertEqual(run_capture("print true as i64\n"), "1\n")
        self.assertEqual(run_capture("print false as i64\n"), "0\n")

    def test_bool_to_bool_no_op(self):
        self.assertEqual(run_capture("print true as bool\n"), "True\n")

    # Prefix `<type>(<expr>)` — equivalent to postfix `as`.

    def test_bool_call_form(self):
        self.assertEqual(run_capture("print bool(5)\n"), "True\n")
        self.assertEqual(run_capture("print bool(0)\n"), "False\n")
        self.assertEqual(run_capture('print bool("")\n'), "False\n")

    def test_i64_call_form(self):
        self.assertEqual(run_capture("print i64(3.7)\n"), "3\n")
        self.assertEqual(run_capture("print i64(true)\n"), "1\n")

    def test_f64_call_form(self):
        self.assertEqual(run_capture("print f64(5)\n"), "5.0\n")

    # Precedence — `as` is the lowest-precedence operator.

    def test_as_lower_than_logical_and(self):
        # `5 and 3 as bool` parses as `(5 and 3) as bool`.
        self.assertEqual(run_capture("print 5 and 3 as bool\n"), "True\n")

    def test_as_lower_than_logical_or(self):
        self.assertEqual(run_capture("print 0 or 0 as bool\n"), "False\n")

    def test_as_lower_than_arithmetic(self):
        # `2 + 3 as f64` → `(2 + 3) as f64` = 5.0.
        self.assertEqual(run_capture("print 2 + 3 as f64\n"), "5.0\n")

    def test_explicit_paren_casts_one_operand(self):
        # `5 and (3 as bool)` casts the 3 to True; result of `5 and True` is True.
        self.assertEqual(run_capture("print 5 and (3 as bool)\n"), "True\n")

    # Cast chains

    def test_cast_chain(self):
        # `3.7 as i64 as f64` = `((3.7 as i64) as f64)` = 3.0
        self.assertEqual(run_capture("print 3.7 as i64 as f64\n"), "3.0\n")

    # The two surface forms are equivalent.

    def test_postfix_and_call_forms_match_for_bool(self):
        a = run_capture("print 7 as bool\n")
        b = run_capture("print bool(7)\n")
        self.assertEqual(a, b)

    def test_postfix_and_call_forms_match_for_i64(self):
        a = run_capture("print 4.9 as i64\n")
        b = run_capture("print i64(4.9)\n")
        self.assertEqual(a, b)


class TestErrors(unittest.TestCase):
    def test_undefined_variable(self):
        with self.assertRaises(RunError):
            run_capture("print x\n")

    def test_two_statements_on_one_line(self):
        with self.assertRaises(ParseError):
            run_capture("set x to 1 set y to 2\n")

    def test_missing_to_after_set(self):
        with self.assertRaises(ParseError):
            run_capture("set x 5\n")

    def test_missing_expression_after_to(self):
        with self.assertRaises(ParseError):
            run_capture("set x to\n")

    def test_stop_outside_loop(self):
        with self.assertRaises(RunError):
            run_capture("stop\n")

    def test_skip_outside_loop(self):
        with self.assertRaises(RunError):
            run_capture("skip\n")

    def test_unterminated_if(self):
        with self.assertRaises(ParseError):
            run_capture("if true\n    print 1\n")


if __name__ == "__main__":
    unittest.main()
