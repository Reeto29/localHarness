#!/usr/bin/env bash
python3 -c "
from geometry import rectangle_area, circle_area
assert rectangle_area(3, 4) == 12, rectangle_area(3, 4)
assert rectangle_area(5, 5) == 25, rectangle_area(5, 5)
# circle_area must be untouched and still correct
assert abs(circle_area(1) - 3.141592653589793) < 1e-9, circle_area(1)
print('ok')
"
