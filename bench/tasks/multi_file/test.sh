#!/usr/bin/env bash
python3 -c "
from textutils import word_count
from report import top_word
wc = word_count('The cat, the dog. The CAT!')
assert wc == {'the': 3, 'cat': 2, 'dog': 1}, wc
assert top_word('apple banana apple banana cherry') == 'apple', top_word('apple banana apple banana cherry')
# tie between 'a' and 'b' -> alphabetical 'a'
assert top_word('b a b a') == 'a', top_word('b a b a')
print('ok')
"
