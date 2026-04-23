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

Only one statement form per situation — `add 5 to total` has no expression-form twin.

### Word operators and symbol operators

Expressions accept both word and symbol forms for arithmetic; the AST is identical either way.

| Word form       | Symbol | Example             |
|-----------------|--------|---------------------|
| `plus`          | `+`    | `a + b`             |
| `minus`         | `-`    | `a - b`             |
| `times`         | `*`    | `a * b`             |
| `divided by`    | `/`    | `a / b`             |

Same precedence rules in both forms: `*` / `/` / `times` / `divided by` bind tighter than `+` / `-` / `plus` / `minus`. Mixing styles in one expression is legal (`2 + 3 times 4` = 14) but keep one style per expression for readability.

**Unary minus** is supported: `-5`, `-x`, `-(a + b)`. There is no `plus` / `+` unary form — just write the number.

**Word form is the default style** for English-like code. Reach for symbols in math-heavy code and where word forms get in the way:

```
repeat for i from 0 to n - 1          # cleaner than 'n minus 1'
set last to xs[length of xs - 1]
set area to width * height
```

**Comparisons** are still word-only for now: `is equal to`, `is greater than`, etc. Symbolic forms (`<`, `>`, `==`, `!=`, `<=`, `>=`) are a deliberate future addition, not a current gap.

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
set first_price to prices[0]      # 0-indexed, C/Python-style
set count to length of prices

set ages to empty map of text to number
set ages["Alice"] to 30
set bob_age to ages["Bob"]
```

Indexing is **0-based**: the first element is `xs[0]`, the last is `xs[(length of xs) minus 1]`. Same convention as C, Python, Rust — closer to how memory works, and avoids subtle off-by-one errors when doing index math. To iterate a list of length `n` by index: `repeat for i from 0 to n minus 1`.

---

## 9b. Matrices — fixed-shape, N-dimensional

Use `matrix` when you want a grid with fixed dimensions and contiguous storage, not a jagged list-of-lists. Indexing uses a single bracket pair with comma-separated indices, COBOL-style.

```
# 2D — 3 rows by 4 columns of numbers
set g to empty matrix 3 by 4 of number
set g[0, 0] to 42
add 1 to g[1, 2]
print g[0, 0]

# introspection
print rows of g           # 3
print columns of g        # 4
print length of g         # 12 (total cells)

# nested loops to fill a multiplication table
set table to empty matrix 5 by 5 of number
repeat for i from 0 to 4
    repeat for j from 0 to 4
        set table[i, j] to (i plus 1) times (j plus 1)
    end
end
```

Higher dimensions work the same way:

```
set cube to empty matrix 10 by 10 by 10 of number
set cube[x, y, z] to value
```

Rules:
- **Fixed shape.** Dimensions are set at creation; you can't grow a matrix. Use a `list` if you need to grow.
- **0-indexed.** `m[0, 0]` is the first cell; matches `list[0]`.
- **Row-major storage.** Under the hood, one flat buffer. A future compiler can swap in SIMD/GPU loops without changing your code.
- **Any element type.** `matrix ... of number`, `matrix ... of text`, `matrix ... of Person` all work.
- **Default values per type:** `number` → 0, `text` → `""`, record/other → `none`.
- **Iteration.** `repeat for each v in m` visits every cell in row-major order. For per-row/per-column work, use nested `repeat for i from 0 to (rows of m) minus 1`.

Matrices vs lists:
- **matrix** — fixed shape, numeric or record grids, math/image/simulation work.
- **list** — variable length, append-heavy, collection of things where order matters but size doesn't.
- **map** — keyed lookups, variable size.

Pick the one that matches intent. Using a list-of-lists for a fixed grid is a code smell.

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
new empty list map matrix of
append
length rows columns
print
true false none
```

~45 words. Everything else is a user identifier or a user-defined operation.

---

## 13. What's explicitly *not* in v1

- No operator symbols (`+ - * /`)
- No alternative phrasings of the same op (`increase x by 5` rejected — use `add 5 to x`)
- No anonymous functions / lambdas
- No classes / inheritance (records only)
- No modules / imports (single-file programs)
- No exceptions (functions return a status, or you check before acting)

Each of these can be added later without breaking v1 code.
