"""Optimal one-to-one assignment (Hungarian method), standard library only (HOM-69).

Used by the diarization evaluator for the DER speaker mapping and the JER mapping.  The
matrix may be rectangular: every row is assigned when there are at least as many columns,
otherwise every column is.  Costs may be ints or ``Fraction`` values; the arithmetic stays
exact for both.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

Number = int | Fraction


def min_cost_assignment(cost: Sequence[Sequence[Number]]) -> dict[int, int]:
    """Row index to column index, minimising the total cost of ``min(rows, cols)`` pairs."""
    n_rows = len(cost)
    n_cols = len(cost[0]) if n_rows else 0
    if n_rows == 0 or n_cols == 0:
        return {}
    if n_rows > n_cols:
        transposed = [[cost[i][j] for i in range(n_rows)] for j in range(n_cols)]
        return {i: j for j, i in min_cost_assignment(transposed).items()}
    # Potentials method, rows <= cols, 1-based with a dummy column 0.
    u: list[Number] = [0] * (n_rows + 1)
    v: list[Number] = [0] * (n_cols + 1)
    match = [0] * (n_cols + 1)  # match[j] = row assigned to column j
    for i in range(1, n_rows + 1):
        match[0] = i
        j0 = 0
        minv: list[Number | None] = [None] * (n_cols + 1)  # None = infinity
        way = [0] * (n_cols + 1)
        used = [False] * (n_cols + 1)
        while True:
            used[j0] = True
            i0 = match[j0]
            delta: Number | None = None
            j1 = 0
            for j in range(1, n_cols + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                mj = minv[j]
                if mj is None or cur < mj:
                    minv[j] = mj = cur
                    way[j] = j0
                if delta is None or mj < delta:
                    delta, j1 = mj, j
            assert delta is not None
            for j in range(n_cols + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    mj = minv[j]
                    if mj is not None:
                        minv[j] = mj - delta
            j0 = j1
            if match[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
    return {match[j] - 1: j - 1 for j in range(1, n_cols + 1) if match[j]}


def max_weight_assignment(weight: Sequence[Sequence[Number]]) -> dict[int, int]:
    """Row index to column index, maximising the total weight."""
    return min_cost_assignment([[-w for w in row] for row in weight])
