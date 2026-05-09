# EuroSciPy 2026 — Social-media copy

These two fields appear later in the pretalx form, are **not visible during
reviews**, and are intended for promoting an accepted poster on social media.
Replace `@YOUR_HANDLE` with your X / BlueSky / Mastodon / LinkedIn handle, and
`@YOUR_COMPANY` with your company / lab page where applicable.

---

## Field A: Super-short summary (X / BlueSky / Fosstadon) — 40–245 chars

Pick whichever you prefer; all fit the 40–245 character limit.

### Option 1 — concise pitch (≈ 230 chars)

```text
Submitted a poster to #EuroSciPy2026: QProgram, a Python DSL for pulse-level quantum experiments. Separates experimental intent from hardware → portable, reproducible, extensible. /by @YOUR_HANDLE #Python #QuantumComputing #SciPy
```

### Option 2 — emphasis on portability (≈ 220 chars)

```text
Pulse-level quantum experiments shouldn't be tied to one vendor. @YOUR_HANDLE will be at #EuroSciPy2026 with a poster on QProgram, a Python DSL that separates experimental intent from hardware. #QuantumComputing #Python
```

### Option 3 — short and punchy (≈ 175 chars)

```text
Heading to #EuroSciPy2026 with a poster on QProgram — a Python DSL for portable, reproducible pulse-level quantum experiments. /by @YOUR_HANDLE #QuantumComputing #Python
```

### Option 4 — research-software angle (≈ 240 chars)

```text
What does reproducible, vendor-independent quantum experiment code look like? @YOUR_HANDLE is presenting QProgram at #EuroSciPy2026 — a Python DSL that lets experiments outlive the control electronics. #ScientificPython #ResearchSoftware
```

---

## Field B: LinkedIn-optimised summary — 300–2,900 chars

LinkedIn favours longer, narrative posts with a hook, structure, and a clear
takeaway. The draft below sits around 2,000 characters with emoji bullets;
shorten or expand to taste. Replace handle/company placeholders before
posting.

```text
Excited to share that I'll be presenting a poster at #EuroSciPy2026 — the European scientific-Python conference! 🇪🇺🐍

The work: QProgram, an open-source Python DSL for pulse-level quantum programming.

Quantum experiments are software artifacts as much as laboratory procedures. But pulse-level code is usually written tightly coupled to a specific instrument vendor, mixing the scientific question, calibration assumptions, hardware channels, and vendor-specific instructions in the same layer of code. The result: experiments are hard to share, reproduce, and move between setups.

QProgram is designed around a simple idea: the experiment should be represented independently from the control system that eventually runs it. Researchers describe what should happen in Python, keep that description inspectable and serialisable, and only later bind it to concrete hardware details.

Why this matters in practice:

🔬 Reproducibility — the same experiment description can be inspected, diffed, validated, and unit-tested.

🔁 Portability — experiments serialise to a versioned, human-readable .qp text format that can be reviewed, stored, or exchanged between teams and labs.

🔌 Extensibility — vendor-specific capabilities (markers, conditional reset, custom acquisition modes…) are added through plugin packages with full IDE typing, without making the core experiment description vendor-dependent.

🧪 Maintainability — when control electronics change, the experiment description stays stable; only the hardware mapping changes.

The poster will walk through the design decisions that make this possible: symbolic parameters and sweeps, typed hardware references, plugin-based vendor extensions, and the .qp file format itself. It also highlights two reusable patterns — typed identifiers that remain strings, and plugin-based extensions with type safety — for anyone building their own scientific-Python libraries.

If you're attending EuroSciPy 2026 in Kraków, please come by the poster session — I'd love to hear what abstractions you're using to keep your hardware-near scientific code reproducible.

By @YOUR_HANDLE @YOUR_COMPANY

#EuroSciPy #EuroSciPy2026 #ScientificPython #Python #QuantumComputing #QuantumSoftware #OpenSource #DSL #ResearchSoftware #Reproducibility #PulseProgramming
```

---

## Notes

- The CFP indicates the conference is in Poland (the deadline is given in
  Poland time). EuroSciPy 2026 is reportedly in Kraków — verify before
  posting and adjust the city / line "in Kraków" if needed.
- For Mastodon/Fosstadon, character limits and conventions are the same as
  the form's 245 limit; the same Option 1–4 work.
- All four short-form options use `@YOUR_HANDLE` as a placeholder. Replace
  with your account on each platform before posting.
- Hashtags chosen for visibility on each platform:
  - `#EuroSciPy2026`, `#EuroSciPy` — conference discoverability
  - `#Python`, `#ScientificPython`, `#SciPy` — primary audience
  - `#QuantumComputing`, `#QuantumSoftware`, `#PulseProgramming` — domain
  - `#OpenSource`, `#ResearchSoftware`, `#Reproducibility`, `#DSL` — themes
