# Migration: waveform parameter rename

**Status:** breaking change. Applies to every QProgram user.

This document explains a renaming pass over four waveform constructors: `Gaussian`,
`GaussianDragCorrection`, `IQDrag`, `Sech`, and `Tukey`. The change is purely cosmetic at the API
level but for `Gaussian` (and the DRAG family that inherits from it) and `Sech` it also changes the
**semantics** of one parameter — what was a dimensionless truncation ratio is now a direct
nanosecond width. The rest of this document explains why, what it costs you, and how to update.

## TL;DR — what to find-and-replace

| Class | Old parameter | New parameter | Conversion formula |
|---|---|---|---|
| `Gaussian` | `num_sigmas` | `sigma` | `sigma = duration / (2 * num_sigmas)` |
| `GaussianDragCorrection` | `num_sigmas` | `sigma` | same as above |
| `GaussianDragCorrection` | `drag_coefficient` | `beta` | (no semantic change, just renamed) |
| `IQDrag` | `num_sigmas` | `sigma` | `sigma = duration / (2 * num_sigmas)` |
| `IQDrag` | `drag_coefficient` | `beta` | (no semantic change) |
| `Sech` | `num_taus` | `tau` | `tau = duration / (2 * num_taus)` |
| `Tukey` | `taper` | `alpha` | (no semantic change, just renamed) |

Concrete numeric examples (the most common case in our codebase):

```python
# Before                                              # After
Gaussian(amplitude=0.5, duration=40, num_sigmas=2.5)  Gaussian(amplitude=0.5, duration=40, sigma=8)
IQDrag(0.5, 40, num_sigmas=2.5, drag_coefficient=0.1) IQDrag(0.5, 40, sigma=8, beta=0.1)
Sech(amplitude=0.5, duration=100, num_taus=4)         Sech(amplitude=0.5, duration=100, tau=12.5)
Tukey(amplitude=0.5, duration=100, taper=0.4)         Tukey(amplitude=0.5, duration=100, alpha=0.4)
```

If you only care about getting your code working again, the four lines above plus the conversion
formulas in the table are all you need.

## Why we made the change

Four independent reasons, each strong enough on its own; together they made the rename worth the
disruption.

### 1. Match the physics literature and major libraries

For DRAG, the parameter Motzoi, Gambetta, Rebentrost, and Wilhelm introduced in
*Phys. Rev. Lett. 103, 110501 (2009)* is called **β**. Every paper that cites DRAG uses β; every
vendor that ships a "DRAG" pulse names the parameter `beta`. Examples in the open-source
ecosystem:

- **Qiskit Pulse** (when it shipped DRAG): `Drag(duration, amp, sigma, beta)`.
- **QuTiP**: `beta` in its DRAG-helper utilities.
- **Quantum Inspire / qBraid / qibo**: `beta`.

Calling it `drag_coefficient` was descriptive but non-standard; anyone arriving from a paper or
another library had to learn a new name for the same number.

For the Tukey window, **`scipy.signal.windows.tukey(M, alpha=0.5)`** is the de-facto reference
implementation. DSP textbooks always write **α**. We had picked `taper` for self-explanatory
naming, but it broke continuity with every other DSP API our users already know.

For Gaussian width, every quantum-control library passes `sigma` directly:

- **Qiskit Pulse**: `Gaussian(duration, amp, sigma)`.
- **QuTiP**: pulse helpers take `sigma`.
- **NMR / EPR literature**: σ is always in time units (ms, μs, ns), never a dimensionless ratio.

### 2. Calibration physics: what the user actually tunes

A typical calibration sweep for a single-qubit gate looks like:

1. Fix duration (set by hardware constraints or sample rate).
2. Sweep amplitude or pulse area until you get a π rotation.
3. Tune sigma (or beta) to minimise leakage.

For a Gaussian pulse, the rotation angle is proportional to the integrated pulse area, which is
approximately `amp · sigma · sqrt(2π)` for a Gaussian well inside its truncation window. In other
words: **sigma is the parameter that matters for calibration physics**. Duration is just a
truncation knob.

With the old `num_sigmas` parameterisation, neither parameter alone corresponded to a physical
quantity:

- `num_sigmas` was a dimensionless truncation ratio (how many σ fit in half the window).
- `sigma` itself was implicit (`duration / (2·num_sigmas)`).

If you sat down to write a calibration loop, you couldn't sweep "the width" directly — you had to
sweep `duration` and `num_sigmas` together to keep one constant, or do the inversion by hand.

With direct `sigma`, the calibration intent maps 1:1 onto code:

```python
sigma_var = p.variable("sigma")
for_loop(sigma_var, 5, 15, 1):
    play(q[0].drive, Gaussian(amp, duration=40, sigma=sigma_var))  # sweeps the pulse width
```

### 3. The "extend the window" use case becomes one knob

The most common reason to change `duration` *without* changing the pulse shape is to add zero-tail
padding around a calibrated pulse — for example, to give the line filter time to settle, to align a
pulse with a clocked trigger boundary, or to insert dead time. With direct `sigma`, that's a
one-parameter change:

```python
# Calibrated π-pulse, 40 ns:
Gaussian(amp, duration=40, sigma=8)

# Same pulse, with 20 ns of tail (e.g. for filter settling):
Gaussian(amp, duration=60, sigma=8)
```

With `num_sigmas`, extending the window from 40 to 60 ns *changed the pulse shape*: the implicit
sigma went from 8 to 12 because `num_sigmas` was held constant. To keep the shape, you had to
recompute `num_sigmas`. This was a frequent source of subtle calibration drift.

### 4. Independent control

Direct `sigma` cleanly separates the two physical knobs:

- `sigma` controls the **shape** (what the pulse looks like).
- `duration` controls the **truncation** (how much of the shape is rendered).

Under the old API the two were coupled: changing one without thinking about the other silently
changed the pulse. Most users were not thinking about it.

## What you lose

`num_sigmas` had one real advantage: it made the shape **scale-invariant** under duration changes.
A single-parameter sweep over `duration` (with `num_sigmas` fixed) would produce a self-similar
family of pulses — same shape, different timescale.

After the rename this requires two coupled changes. The clean idiom is to parameterise on `sigma`
and derive `duration` from it:

```python
# Scaling on sigma, keeping a 5-sigma truncation window:
sigma = p.variable("sigma")
g = Gaussian(amp, duration=5*sigma, sigma=sigma)
```

That's still one knob to sweep; you've just inverted the naming. If you genuinely want fixed
shape over a `duration` sweep — uncommon in our experience — a thin convenience helper covers
the case:

```python
def gaussian_at(amp: float, duration: int, num_sigmas: float = 2.5) -> Gaussian:
    """Compatibility helper for the old `num_sigmas` semantics."""
    return Gaussian(amplitude=amp, duration=duration, sigma=duration / (2 * num_sigmas))
```

Keep it local to your script if you need it; we are not shipping it from the library.

## Migration recipes

### Updating Python code

The conversion is purely arithmetic. For every existing call:

1. `Gaussian(amp, dur, num_sigmas=N)` → `Gaussian(amp, dur, sigma=dur / (2 * N))`.
2. `GaussianDragCorrection(amp, dur, num_sigmas=N, drag_coefficient=B)` →
   `GaussianDragCorrection(amp, dur, sigma=dur / (2 * N), beta=B)`.
3. `IQDrag(amp, dur, num_sigmas=N, drag_coefficient=B)` →
   `IQDrag(amp, dur, sigma=dur / (2 * N), beta=B)`.
4. `Sech(amp, dur, num_taus=N)` → `Sech(amp, dur, tau=dur / (2 * N))`.
5. `Tukey(amp, dur, taper=A)` → `Tukey(amp, dur, alpha=A)`.

The most common case in the existing codebase was `duration=40, num_sigmas=2.5` →
`duration=40, sigma=8`. Three other duration values appear in test fixtures and produce
`duration=2000 → sigma=400`, `duration=100 → sigma=20`, and `duration=20 → sigma=4`.

If you maintain a substantial codebase, the following one-shot rewrite covers the keyword forms
(positional `Gaussian(0.5, 40, 2.5)` will need a manual pass since the third arg's meaning
changed):

```python
import re
import pathlib

KEYWORD_REWRITES = [
    (r"duration=40, num_sigmas=2\.5",   "duration=40, sigma=8"),
    (r"duration=20, num_sigmas=2\.5",   "duration=20, sigma=4"),
    (r"duration=100, num_sigmas=2\.5",  "duration=100, sigma=20"),
    (r"duration=2000, num_sigmas=2\.5", "duration=2000, sigma=400"),
    (r"drag_coefficient=",              "beta="),
    (r"\btaper=",                       "alpha="),
    # Note: num_taus has no single-value rewrite — Sech is rare enough to convert by hand.
]

for p in pathlib.Path(".").rglob("*.py"):
    text = p.read_text()
    new = text
    for pat, rep in KEYWORD_REWRITES:
        new = re.sub(pat, rep, new)
    if new != text:
        p.write_text(new)
```

Run it once, then read the diff before committing — the script is intentionally conservative and
will miss anything that doesn't match the exact pattern.

### Updating `.qp` files

`.qp` files emitted by the old serializer use the old field names. They will fail to parse against
the new code. You have two options:

1. **Re-emit from Python**: re-run the producing script with the new code; the writer now emits
   `sigma=` and `beta=` and `alpha=`. This is the safest option.
2. **Manually edit** if you only have the file: replace `num_sigmas=N` with `sigma=<duration / (2*N)>`,
   `drag_coefficient=B` with `beta=B`, `taper=A` with `alpha=A`, `num_taus=N` with
   `tau=<duration / (2*N)>`. The conversion is local to each waveform constructor call.

Note that the `.qp` format does **not** carry version information for individual operation surfaces
— there is no "this file uses the old parameter names" header. The format's `#!QProgram 1.0`
header tracks the overall grammar, not the parameter names of individual waveforms. We accept this
as a one-time migration cost; future-proofing the format against this kind of rename is not on
the roadmap.

## Trade-offs we accepted

We knew this would break existing experimental scripts. We chose to pay the cost because:

- **Pre-1.0 status**. QProgram is unreleased; backward-compatibility is not a contract we have
  with anyone yet. The breakage cost is bounded to current internal users; the benefit is the
  entire future user base.
- **Standard names age well**. Five years from now nobody will remember the old names. The
  literature names (`sigma`, `tau`, `beta`, `alpha`) will still be the literature names.
- **The semantic shift was overdue**. The `num_sigmas` parameterisation hid the calibration knob
  (sigma) behind a derived quantity. Every team that adopted QProgram and tried to do a
  width-only sweep hit the same surprise. Renaming without changing semantics would have papered
  over the real issue.
- **Internal users only**. We have no third-party `.qp` files in the wild and no public API
  contract to preserve. The migration is a one-time cost paid once.

We did *not* ship a deprecation period (a release where both names work and the old one warns).
Pre-1.0 we'd rather do the rename cleanly and update internal code than carry two names through
the rest of the alpha period.

## References

### DRAG parameter
- Motzoi, Gambetta, Rebentrost, Wilhelm.
  *Simple Pulses for Elimination of Leakage in Weakly Nonlinear Qubits.*
  Phys. Rev. Lett. **103**, 110501 (2009).
  [doi:10.1103/PhysRevLett.103.110501](https://doi.org/10.1103/PhysRevLett.103.110501).

### Library precedent
- `scipy.signal.windows.tukey(M, alpha, sym)` —
  [scipy docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.windows.tukey.html).
- Qiskit Pulse `Drag` and `Gaussian` builders (Qiskit ≤ 1.x — Pulse was removed in 2.0).
- QuTiP control-pulse helpers (`qutip.control.pulseoptim`).

### Background reading
- *Quantum Control of Open Systems* (Wiseman & Milburn), Ch. 5 — adiabatic passage, `sech` pulses.
- *Spin Dynamics* (Levitt) — NMR conventions for Gaussian and Hann pulses.

## See also

- [Waveforms guide](../guide/waveforms.md) — the user-facing reference, now reflecting the new
  parameter names.
- [Adding waveforms](../developer/adding-waveforms.md) — for vendor authors writing custom
  waveform classes.
