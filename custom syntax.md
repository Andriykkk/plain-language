Extensible PlainLang — design for a tree-walking interpreter
You're describing a small core with a user-extensible surface. The right model for this has three pieces, all well-understood:

Sentence-based calls (Smalltalk / Forth / Inform 7)
Multiple dispatch (Julia / Common Lisp CLOS) — same name, different impls by argument types
Pattern-based syntax extension (Rebol / Racket macros, simplified) — users declare surface forms
Here's how they fit together.

1. The core model: a sentence is a pattern call
A line is a pattern plus slot values. The pattern add <a> to <b> with slots 5 and total becomes a call to an operation named add ... to ... with arguments (5, total).

The interpreter holds two registries:

Pattern registry — maps surface patterns to operation names
Operation registry — maps (operation_name, arg_types) to implementations
Built-in patterns you ship with the language:


set <name> to <expr>
if <expr> ... end
repeat ... end
define operation "<pattern>" for <types> ... end
Everything else — including add, multiply, list indexing, printing — is a user-level operation defined in the language itself.

2. Defining an operation (with overload)
The user writes:


define operation "add <a> and <b>" for numbers
    return a plus b
end

define operation "add <a> and <b>" for vectors
    set result to empty list
    repeat for each i from 0 to length of a
        append a[i] plus b[i] to result
    end
    return result
end
Both declarations register the same pattern. The dispatcher picks which body to run based on the runtime types of a and b. This is C++ overloading / Julia multiple dispatch.

When the user later writes:


set c to add v1 and v2
The interpreter:

Matches the pattern add <a> and <b> → operation name add/and
Evaluates the slots: v1, v2
Checks types → both are vectors
Calls the vectors implementation
3. Custom syntax for the same underlying operation
The user can add alternative surface forms that point to the same operation:


define operation "<a> plus <b>" for numbers
    return add a and b
end

define operation "multiply_vectors <a> and <b>" for vectors
    return multiply a and b
end
These are pure syntax sugar — they expand into calls to other operations. A user who prefers math-ish style gets a plus b; a user who prefers verbose gets add a and b.

This is the whole "small core, everything else on top" idea: your core defines maybe 30 patterns, and users (or a standard library written in the language) add the rest.

4. The ambiguity problem — and the v1 rule
The real danger: if two patterns can match the same token sequence, behavior is unpredictable. AppleScript's collapse was caused by this.

v1 rule: require parentheses when nesting pattern calls.

Bad (ambiguous):


add multiply x and y and z
Good:


add (multiply x and y) and z
Inside parens, the parser recurses. Outside, it tries patterns left-to-right, longest-match-first. If two patterns tie, fail loudly with an error listing both candidates. Don't guess.

Later, once you have real users, you can add conventions (precedence tiers, "infix" pattern attributes). Don't pre-design those.

5. Parsing strategy — pattern matching, not a grammar
A real grammar with user-extensible productions is an LR-parser nightmare. Skip it. Use longest-match pattern scanning instead:

Given a line of tokens, to parse an expression:

Look at all registered patterns
Try each one against the current token position
For each <slot> in a pattern, recursively parse an expression — but expressions inside a slot stop at the next literal token of the outer pattern
Pick the pattern that consumes the most tokens and still succeeds
If two tie, error
This is how Forth-like and Rebol-like languages work. It's a few hundred lines of Python.