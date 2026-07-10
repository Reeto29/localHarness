#!/usr/bin/env bash
python3 -c "
from calc import evaluate

def approx(a, b): return abs(a - b) < 1e-9

# precedence
assert approx(evaluate('2 + 3 * 4'), 14.0), evaluate('2 + 3 * 4')
assert approx(evaluate('2 + 3 * 4 - 1'), 13.0), evaluate('2 + 3 * 4 - 1')
# parentheses + nesting
assert approx(evaluate('(2 + 3) * 4'), 20.0), evaluate('(2 + 3) * 4')
assert approx(evaluate('((1 + 2) * (3 + 4))'), 21.0), evaluate('((1 + 2) * (3 + 4))')
# division -> float
assert approx(evaluate('10 / 4'), 2.5), evaluate('10 / 4')
# decimals + whitespace
assert approx(evaluate('  3.5 + 1.5 '), 5.0), evaluate('  3.5 + 1.5 ')
# unary minus, the hard part
assert approx(evaluate('-3'), -3.0), evaluate('-3')
assert approx(evaluate('2 * -3'), -6.0), evaluate('2 * -3')
assert approx(evaluate('-(1 + 2)'), -3.0), evaluate('-(1 + 2)')
assert approx(evaluate('-2 + 5'), 3.0), evaluate('-2 + 5')

# errors
for bad in ['2 +', '1 2', '(1 + 2', ')', '2 @ 3']:
    try:
        evaluate(bad)
        raise AssertionError('expected error for: ' + bad)
    except (ValueError, ZeroDivisionError):
        pass

try:
    evaluate('1 / 0')
    raise AssertionError('expected ZeroDivisionError')
except ZeroDivisionError:
    pass

print('ok')
"
