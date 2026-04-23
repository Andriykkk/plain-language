# PlainLang Surface Syntax — v0.1

COBOL/ABC-blended syntax. One way per thing — no alternative phrasings of the same operation. Word-based operators throughout; `.` is the only symbol that survives in core syntax.

---

## 1. Comments and literals

```
# this is a comment

set x to 42                 # number
set greeting to "hello"     # text
set flag to true            # truth value (true / false)
set nothing to none         # absence of value
```

---

## 2. Variables and types

Types are inferred by default; declare explicitly when you want the checker to verify:

```
set total to 0
set total to 0 as number
set name to "Alice" as text

set prices to empty list of number
set ages to empty map of text to number
```

Reassignment uses the same form:

```
set total to 100
```

---

## 3. Arithmetic — sentence form for simple, expression form for compound

Simple ops read like English:

```
add 5 to total
subtract 10 from total
multiply total by 2
divide total by 4
```

When you need a compound expression, put it inside `set`:

```
set total to (price times quantity) plus tax
set average to sum divided by count
```

Only one form per situation — `add 5 to total` has no expression-form twin.

**Word-based operators only** — `plus`, `minus`, `times`, `divided by`. No `+ - * /`.

---

## 4. Comparisons

```
is equal to
is not equal to
is greater than
is less than
is at least            # >=
is at most             # <=
```

Example:

```
if total is greater than 100
    ...
end
```

---

## 5. Branches

```
if total is greater than 100
    print "large"
else if total is greater than 10
    print "medium"
else
    print "small"
end
```

### Block terminators — bare `end` or `end <kind>`

Any block may be closed with bare `end`, or with the two-token form naming the block kind:

```
if x is greater than 0
    print "positive"
end if

repeat 10 times
    print "hi"
end repeat
```

Mixing styles across a file is allowed. Mismatched kinds are a compile error:

```
if x is greater than 0
    print "oops"
end repeat        # error: 'if' cannot be closed with 'end repeat'
```

The valid kinds are `if`, `repeat`, `function`, `record`.

---

## 6. Loops — four distinct idioms

```
repeat 10 times
    print "hi"
end

repeat for each price in prices
    add price to total
end

repeat for i from 1 to 10
    print i
end

repeat while total is less than 100
    add 1 to total
end
```

Each form covers a case the others don't, so there's no redundancy.

Control inside a loop:

```
stop                 # break
skip                 # continue
```

### Note on `times` in `repeat N times`

The word `times` is both the multiplication operator (`price times quantity`) and the iteration marker (`repeat 10 times`). At the top level of a `repeat` count, `times` is always the loop marker. For multiplication in the count, wrap it in parentheses:

```
repeat 10 times            # 10 iterations
repeat 2 plus 3 times      # 5 iterations
repeat (2 times 3) times   # 6 iterations — parens needed
```

---

## 7. Functions

Definition:

```
define function calculate_total
    input prices as list of number
    output as number

    set total to 0
    repeat for each price in prices
        add price to total
    end
    return total
end
```

Call:

```
set result to call calculate_total with prices
```

`call ... with ...` is the universal function-call form. Multiple arguments separated by `and`:

```
set area to call rectangle_area with width and height
```

No arguments:

```
set now to call current_time
```

Single-word function names for v1. Multi-word names via the pattern system (see `design-notes.md` §5) come later.

---

## 8. Records (structs)

```
define record Person
    name as text
    age as number
    balance as number
end

set user to new Person
set user.name to "Bob"
set user.age to 30
add 100 to user.balance
```

Field access uses `.` — the one symbol that survived.

---

## 9. Lists and maps

```
set prices to empty list of number
append 10 to prices
append 20 to prices
set first_price to prices[1]      # 1-indexed, COBOL/Lua/Julia-style
set count to length of prices

set ages to empty map of text to number
set ages["Alice"] to 30
set bob_age to ages["Bob"]
```

One-indexed lists fits the "readable to non-programmers" audience.

---

## 10. Output

```
print "hello"
print total
print "total is" and total
```

`and` in `print` joins values with a space.

---

## 11. Putting it together — a small real program

```
define record Order
    customer as text
    amount as number
end

define function total_of
    input orders as list of Order
    output as number

    set total to 0
    repeat for each order in orders
        add order.amount to total
    end
    return total
end

set orders to empty list of Order

set o1 to new Order
set o1.customer to "Alice"
set o1.amount to 100
append o1 to orders

set o2 to new Order
set o2.customer to "Bob"
set o2.amount to 250
append o2 to orders

set grand_total to call total_of with orders

if grand_total is greater than 300
    print "big order batch" and grand_total
else
    print "small batch" and grand_total
end
```

---

## 12. Reserved word list (final — keep it small)

```
set to as
add subtract multiply divide by from
plus minus times divided
is equal not greater less than at least most
if else end
repeat times for each in from to while
stop skip
define function record input output return
call with and
new empty list map of
print
true false none
```

~40 words. Everything else is a user identifier or a user-defined operation.

---

## 13. What's explicitly *not* in v1

- No operator symbols (`+ - * /`)
- No alternative phrasings of the same op (`increase x by 5` rejected — use `add 5 to x`)
- No anonymous functions / lambdas
- No classes / inheritance (records only)
- No modules / imports (single-file programs)
- No exceptions (functions return a status, or you check before acting)

Each of these can be added later without breaking v1 code.
