#!/usr/bin/env bash
python3 -c "
from palindrome import is_palindrome
assert is_palindrome('A man, a plan, a canal: Panama') is True
assert is_palindrome('racecar') is True
assert is_palindrome('hello') is False
assert is_palindrome('') is True
print('ok')
"
