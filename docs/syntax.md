# PlainLang Surface Syntax — v0.1

COBOL/ABC-blended syntax. One way per thing — no alternative phrasings of the same operation. Word-based operators throughout; `.` is the only symbol that survives in core syntax. Math-flavored code can opt into the symbol forms (`+ - * /`, bitwise, comparisons) where word forms get heavy.

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

Expressions accept both word and symbol forms; the AST is identical either way.

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

Symbolic comparison forms (`<`, `>`, `==`, `!=`, `<=`, `>=`) are a deliberate future addition, not a current gap.

---

## 5. Boolean logic — `and`, `or`, `not`

For combining boolean conditions inside `if`, `while`, etc.:

```
if x is greater than 0 and y is less than 10
    print "in range"
end

if name is equal to "" or name is equal to "guest"
    print "anonymous"
end

if not (x is equal to 0)
    print "non-zero"
end
```

Symbol forms are accepted too — pick whichever reads better for your code:

| Word form | Symbol |
|-----------|--------|
| `and`     | `&&`   |
| `or`      | `\|\|` |
| `not`     | `!`    |

`not` / `!` is prefix; `and` / `&&` and `or` / `||` are infix.

### Operands and result

`and` / `or` accept any operands that share a common type:
- both BOOL
- both numeric (different widths get promoted, e.g. i8 + i64 → i64)
- both TEXT
- both REF (lists, matrices, record pointers)

Operands of *unrelated* types (`i64 and text`, `bool or i64`, …) are a compile error. The result has the common type of the operands.

### Truthiness

When operands aren't already BOOL, they're treated as truthy/falsy by their natural test:

| Type     | Falsy when         |
|----------|--------------------|
| BOOL     | `false`            |
| numeric  | `0` (or `0.0`)     |
| TEXT     | empty (`length` 0) |
| REF      | empty / `none`     |
| NONE     | always             |

### Short-circuit and Python-style return value

`and` / `or` are short-circuit: the right operand is not evaluated when the left already determines the answer. They return the *value* of the chosen operand, not a generic bool — Python semantics:

```
set name to user_name or "anonymous"          # default-when-empty
set first_char to name and name[0]            # only index when name is non-empty
```

`a and b` returns `a` if `a` is falsy, otherwise `b`. `a or b` returns `a` if `a` is truthy, otherwise `b`. The result is type-stable because both operands are guaranteed to share a common type.

### Don't confuse logical and bitwise

| | Logical | Bitwise |
|-|---------|---------|
| AND | `and`, `&&` | `bit_and`, `&` |
| OR | `or`, `\|\|` | `bit_or`, `\|` |
| NOT | `not`, `!` | `bit_not`, `~` |
| XOR | (rare; use `is not equal to`) | `xor`, `^` |

Logical ops short-circuit and care about truthiness. Bitwise ops always evaluate both operands and only accept integers.

---

## 5b. Bitwise — integers only

For low-level bit manipulation. Both word and symbol forms work:

| Word form       | Symbol | Example             |
|-----------------|--------|---------------------|
| `bit_and`       | `&`    | `flags bit_and mask` |
| `bit_or`        | `\|`   | `flags \| new_flag` |
| `xor`           | `^`    | `a xor b`           |
| `bit_not`       | `~`    | `bit_not value`     |
| `shifted left by`  | `<<`   | `1 << 8` |
| `shifted right by` | `>>`   | `value shifted right by 4` |

Bitwise ops require **integer operands**; passing a float is a compile error. Mixed-width integers are promoted to the wider type (i8 + i64 → i64).

Precedence (low → high): comparison < `bit_or` (`|`) < `xor` (`^`) < `bit_and` (`&`) < shift (`<< >>`) < addition < multiplication. Matches Python and C. When mixing with arithmetic, parenthesize for clarity.

```
set masked to value bit_and 0x_FF
set high_byte to (value shifted right by 8) bit_and 0x_FF
set toggled to flags xor (1 << bit_index)
```

---

## 6. Branches

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

## 7. Loops — four distinct idioms

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

## 8. Functions

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

`call ... with ...` is the universal function-call form. Multiple arguments are separated by **commas**:

```
set area to call rectangle_area with width, height
set greeting to call format_name with first, middle, last
```

No arguments:

```
set now to call current_time
```

`and` is reserved for boolean logic and never appears in argument lists; the comma is the only separator. This frees `and` to mean "logical AND" everywhere it appears in an expression.

Single-word function names for v1. Multi-word names via the pattern system (see `extension-system.md`) come later.

---

## 9. Records (structs)

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

Records may contain other records as fields; nested records are stored **inline** (not as pointers), so `person.address.zip` is a chain of compile-time offsets, not a chain of pointer dereferences.

---

## 10. Lists and maps

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

Lists of records store the records **inline**. `length of xs` returns the number of records, not the number of slots. `xs[i].field` reads the field's slot directly via offset arithmetic — no per-element pointer indirection.

---

## 10b. Matrices — fixed-shape, N-dimensional

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
- **Default values per type:** `number` → 0, `text` → `""`, record/other → recursively zeroed.
- **Iteration.** `repeat for each v in m` visits every cell in row-major order. For per-row/per-column work, use nested `repeat for i from 0 to (rows of m) minus 1`.

Matrices vs lists:
- **matrix** — fixed shape, numeric or record grids, math/image/simulation work.
- **list** — variable length, append-heavy, collection of things where order matters but size doesn't.
- **map** — keyed lookups, variable size.

Pick the one that matches intent. Using a list-of-lists for a fixed grid is a code smell.

---

## 11. Output

```
print "hello"
print total
print "total is", total
print "name:", user.name, "age:", user.age
```

`print` joins its arguments with a single space and ends with one newline. Arguments are separated by **commas**, the same as in function calls. `and` is never used as a separator — it's reserved for boolean logic.

---

## 12. Putting it together — a small real program

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

if grand_total is greater than 300 and length of orders is at least 2
    print "big order batch", grand_total
else
    print "small batch", grand_total
end
```

---

## 13. Reserved word list

```
set to as
add subtract multiply divide by from
plus minus times divided
is equal not greater less than at least most
and or                           # logical (also && and ||)
bit_and bit_or bit_not xor       # bitwise (also & | ~ ^ << >>)
shifted left right
if else end
repeat times for each in from to while
stop skip
define function record input output return
call with
new empty list map matrix of
append
length rows columns
print
true false none
```

~55 words. Everything else is a user identifier or a user-defined operation.

`and` is reserved for boolean logic only — never as a list/argument separator.

---

## 14. What's explicitly *not* in v1

- No anonymous functions / lambdas
- No classes / inheritance (records only)
- No modules / imports (single-file programs)
- No exceptions (functions return a status, or you check before acting)
- No symbolic comparison operators (`<`, `==`, etc.) — coming later

Each of these can be added later without breaking v1 code.
