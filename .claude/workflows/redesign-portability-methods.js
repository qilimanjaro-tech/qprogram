export const meta = {
  name: 'redesign-portability-methods',
  description: 'Design replacements for QProgram.with_bus_mapping/with_waveforms using schema+BusRef+fragments',
  phases: [
    { title: 'Understand', detail: 'parallel deep-read of overlapping mechanisms' },
    { title: 'Design', detail: '4 independent design approaches' },
    { title: 'Judge', detail: 'score designs on a fixed rubric' },
    { title: 'Verify', detail: 'stress top designs against concrete scenarios' },
    { title: 'Synthesize', detail: 'final recommendation doc' },
  ],
}

const ROOT = '/home/fedonman/projects/demos/qprogram-decoupling'

const UNDERSTAND_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['mechanism', 'whatItDoes', 'overlapWithBusMapping', 'overlapWithWaveforms', 'gaps', 'keyFacts'],
  properties: {
    mechanism: { type: 'string' },
    whatItDoes: { type: 'string' },
    overlapWithBusMapping: { type: 'string', description: 'How/whether this could replace with_bus_mapping, and what it cannot do' },
    overlapWithWaveforms: { type: 'string', description: 'How/whether this could replace with_waveforms, and what it cannot do' },
    gaps: { type: 'array', items: { type: 'string' }, description: 'Concrete limitations / missing pieces' },
    keyFacts: { type: 'array', items: { type: 'string' }, description: 'Precise facts with file:line refs that a designer must know' },
  },
}

phase('Understand')
const READERS = [
  {
    label: 'fragments+parameters',
    prompt: `Read ${ROOT}/qprogram/src/qprogram/fragments.py and ${ROOT}/qprogram/src/qprogram/operations/call.py in full, plus the fragment sections of ${ROOT}/.specs/qprogram-dsl.md (search "fragment", "parameter", "Parameter", "expand").
Question: Fragments use parameter() placeholders that bind buses/waveforms/values at the call site, then expand() lowers them with substitution + channel/acquires re-validation. Could a "program as a parameterized template" model (fragments, or a fragment-like wrapper of the whole program) REPLACE with_bus_mapping (rewrite bus strings) and with_waveforms (resolve string aliases to concrete waveforms)?
Determine precisely: (1) Can a fragment parameter stand in BUS position and WAVEFORM position? (2) Is the TOP-LEVEL program parameterizable, or only sub-fragments? (3) What re-validation happens on bind (channel/acquires)? (4) What would it take to bind the whole program's buses+waveforms at "execution time" the way calibration is described? Return the schema.`,
  },
  {
    label: 'schema+BusRef+naming',
    prompt: `Read ${ROOT}/qprogram/src/qprogram/buses.py in full and the bus-handling parts of ${ROOT}/qprogram/src/qprogram/qprogram.py (search "schema", "BusRef", "_validate_bus", "with_bus_mapping", "_remap_buses") and the writer/parser schema handling in ${ROOT}/qprogram/src/qprogram/serialization/writer.py and parser.py (search "schema", "BusRef", "BUS_ATTRS").
CRITICAL question to answer with certainty: when an op is built with a BusRef (q[0].drive), is the metadata (element/idx/kind/channel/acquires/schema) RETAINED on the AST node (op.bus), and does it survive deepcopy? Quote the relevant code.
Then: Given BusRef carries element/idx/kind and BusNaming is a template, could "bus remapping" be re-expressed as STRUCTURAL rebinding — e.g. re-resolve every BusRef under a new index (q0->q1), a new element, or a different BusNaming/schema — instead of a flat dict[str,str]? What about raw-string buses (no metadata)? What breaks on round-trip? Return the schema.`,
  },
  {
    label: 'platform+calibration',
    prompt: `Read ${ROOT}/qprogram/src/qprogram/executor.py (focus run/execute, parameters, set_parameter/get_parameter, the env) and ${ROOT}/qprogram/src/qprogram/platform.py, and ${ROOT}/my-platform/src/my_platform/platform.py and schema.py. Also search the spec ${ROOT}/.specs/qprogram-dsl.md for "calibration", "with_waveforms", "alias", "parameter store".
Question: The spec frames with_waveforms as "how calibration data is applied — the program uses string aliases, concrete waveforms are provided externally by the platform/calibration system at execution time." TODAY: how does a platform inject calibration? Is there ANY notion of a platform-provided WAVEFORM LIBRARY / alias resolution, or only the scalar parameters dict + set_parameter/get_parameter? Could execute()/run() resolve waveform aliases from a platform-held library automatically (so the alias is the portable thing and the concrete pulse comes from the platform's calibration), instead of the user manually calling with_waveforms first? What signature/hook would that need? Return the schema.`,
  },
  {
    label: 'serialization+roundtrip',
    prompt: `Read ${ROOT}/.specs/qp-file-format.md (search "alias", "with_waveforms", "schema", "BusRef", "quoted") and ${ROOT}/qprogram/src/qprogram/serialization/writer.py + parser.py for how (a) string waveform aliases and (b) schema-backed bus paths vs raw-string buses serialize.
Question: A portability redesign must round-trip through .qp. Establish: (1) How is a string waveform alias written and re-read (quoting)? (2) How is a BusRef written (path form) vs a raw string (quoted), and is the schema declared once at top? (3) If we made waveform aliases bus-scoped, or made bus remapping structural via schema, what NEW serialization would be required, and what would stay free? (4) Does source_map / expand() interact? Return the schema. Be concrete about what currently round-trips for free vs what a redesign would add to the grammar/format.`,
  },
  {
    label: 'usecases+tests+vendors',
    prompt: `Read the tests ${ROOT}/qprogram/tests/test_qprogram.py (the with_bus_mapping/with_waveforms tests ~line 497-564) and ${ROOT}/qprogram/tests/test_round_trip.py (~265-280). Grep the whole repo (qprogram, qprogram-qblox, qprogram-qdac, my-platform) for with_bus_mapping/with_waveforms usage. Read spec ${ROOT}/.specs/qprogram-dsl.md section 2.2 (Mappings) and ~line 1590-1600.
Question: Enumerate EXACTLY the distinct real use cases these two methods serve (e.g. port-to-different-qubit, cross-platform naming, calibration injection, alias indirection for stability). For EACH use case, note who the actor is (user / platform / calibration system) and WHEN it happens (build / pre-execute / execute). Also: do the vendor packages depend on these methods at all? This grounds the design in what must NOT be lost. Return the schema (put the enumerated use cases in keyFacts).`,
  },
]
const findings = (await parallel(READERS.map(r => () =>
  agent(r.prompt, { label: r.label, phase: 'Understand', schema: UNDERSTAND_SCHEMA })
))).filter(Boolean)

const findingsBrief = findings.map(f =>
  `### ${f.mechanism}\nWHAT: ${f.whatItDoes}\nvs with_bus_mapping: ${f.overlapWithBusMapping}\nvs with_waveforms: ${f.overlapWithWaveforms}\nGAPS: ${(f.gaps||[]).join(' | ')}\nFACTS: ${(f.keyFacts||[]).join(' | ')}`
).join('\n\n')

log(`Understand done: ${findings.length} findings`)

// ---- Design ----
phase('Design')
const DESIGN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['name', 'oneLiner', 'approach', 'apiSketch', 'busPortability', 'waveformPortability',
             'schemaLeverage', 'roundTrip', 'fragmentInteraction', 'migrationCost', 'specImpact', 'pros', 'cons'],
  properties: {
    name: { type: 'string' },
    oneLiner: { type: 'string' },
    approach: { type: 'string', description: 'Detailed mechanism — what objects/methods, where they live, how binding happens' },
    apiSketch: { type: 'string', description: 'Concrete Python API + a .qp snippet if relevant' },
    busPortability: { type: 'string', description: 'How a program moves q0->q1 and across platform naming' },
    waveformPortability: { type: 'string', description: 'How waveforms become portable (per-bus/per-element resolution) — answer the user prompt (a)' },
    schemaLeverage: { type: 'string', description: 'How it uses BusRef/schema/naming templates — answer prompt (b)' },
    roundTrip: { type: 'string', description: 'How it serializes / survives .qp round-trip' },
    fragmentInteraction: { type: 'string' },
    migrationCost: { type: 'string', description: 'What changes in code/tests/specs; is it pre-1.0 cheap?' },
    specImpact: { type: 'string' },
    pros: { type: 'array', items: { type: 'string' } },
    cons: { type: 'array', items: { type: 'string' } },
  },
}
const ANGLES = [
  { label: 'minimal-evolution', angle: `MINIMAL-EVOLUTION angle: keep the post-hoc "return a new program" shape but modernize it. Make bus remapping SCHEMA-AWARE (rebind BusRefs structurally by index/element/naming, re-resolving via the schema, with raw strings still handled by an optional explicit map) and make waveform resolution BUS-SCOPED so the same alias resolves to different concrete pulses per element/qubit (answering "waveforms should also be portable"). Smallest possible departure from today.` },
  { label: 'schema-rebind-centric', angle: `SCHEMA-CENTRIC angle: treat the schema as the single source of truth for portability. A program is bound to schema S1; "porting" is re-resolving the whole AST against a DIFFERENT schema/naming or a re-indexing map (q0->q1) computed from BusRef metadata. Push bus remapping entirely into schema operations. Consider whether with_bus_mapping disappears entirely, replaced by schema rebinding. Address how waveforms ride along (per-element calibration keyed by BusRef metadata).` },
  { label: 'fragment-unification', angle: `FRAGMENT-UNIFICATION angle: there are TWO parameterization systems (fragments and with_*). Unify them. Could the whole program be (or be wrapped as) a parameterized template whose buses AND waveforms are parameter() placeholders bound at execution time? Lean on expand()'s existing substitution + channel/acquires re-validation. Show how "calibration injection" becomes "binding the template". Be honest about top-level-program limitations found in Understand.` },
  { label: 'platform-calibration-library', angle: `PLATFORM-CALIBRATION angle: the spec says concrete waveforms come from "the platform / calibration system at execution time". Make the PLATFORM own a calibration/waveform library: aliases stay in the program (portable by construction), and execute()/run() resolves them per-bus from the platform's library — no manual with_waveforms pass. Bus portability comes from the platform's schema/naming. The program stays abstract; binding is the platform's job. Address building/explain/validate without a platform.` },
]
const designs = (await parallel(ANGLES.map(a => () =>
  agent(
    `You are designing a replacement for QProgram's legacy with_bus_mapping(dict[str,str]) and with_waveforms(dict[str,Waveform]) methods.

CONTEXT — the user's exact ask:
"with_bus_mapping and with_waveforms are old, carried over from a previous implementation. Think of ways to implement what they achieve with the CURRENT state of QProgram: (a) waveforms should ALSO be portable; (b) we now have BusRef and schema naming templates — maybe bus mappings should target schema elements? Or are they needed at all?"

The project is PRE-1.0: any breaking change is acceptable; do NOT gate by backward-compat or cost. Optimize for the RIGHT design.

WHAT THE LEGACY METHODS DO:
- with_bus_mapping: deepcopy, walk AST, rewrite bus-name strings via a flat dict (uses each op's BUS_ATTRS; handles Sync's list). Schema-unaware.
- with_waveforms: deepcopy, walk AST, replace string waveform ALIASES ("pi_pulse") with concrete Waveform/IQWaveform (uses WAVEFORM_ATTRS). The alias is GLOBAL, not bus-scoped — so q0 and q1 sharing "pi_pulse" cannot get different pulses.

FINDINGS FROM CODEBASE ANALYSIS:
${findingsBrief}

Produce ONE coherent design from this specific angle:
${a.angle}

Requirements your design MUST address head-on: (a) make waveforms portable (per-bus/element resolution, so porting q0->q1 can use q1's calibration); (b) decide whether flat bus mappings should be replaced by schema-element-targeted rebinding, or eliminated entirely. Give a concrete Python API sketch and, where relevant, a .qp snippet. Be specific about WHERE binding happens (build/pre-execute/execute) and WHAT re-validation runs. Return the schema.`,
    { label: a.label, phase: 'Design', schema: DESIGN_SCHEMA }
  )
))).filter(Boolean)

log(`Design done: ${designs.length} designs`)

// ---- Judge ----
phase('Judge')
const allDesigns = designs.map((d, i) =>
  `## DESIGN ${i}: ${d.name}\n${d.oneLiner}\nAPPROACH: ${d.approach}\nAPI: ${d.apiSketch}\nBUS-PORT: ${d.busPortability}\nWF-PORT: ${d.waveformPortability}\nSCHEMA: ${d.schemaLeverage}\nROUNDTRIP: ${d.roundTrip}\nFRAGMENTS: ${d.fragmentInteraction}\nMIGRATION: ${d.migrationCost}\nSPEC: ${d.specImpact}\nPROS: ${(d.pros||[]).join('; ')}\nCONS: ${(d.cons||[]).join('; ')}`
).join('\n\n')

const JUDGE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['ranking', 'rationale', 'bestHybrid'],
  properties: {
    ranking: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['designIndex', 'name', 'scores', 'total', 'killerFlaw'],
        properties: {
          designIndex: { type: 'integer' },
          name: { type: 'string' },
          scores: {
            type: 'object',
            additionalProperties: false,
            required: ['busPortability', 'waveformPortability', 'schemaLeverage', 'roundTrip', 'simplicity', 'fragmentFit', 'specFit'],
            properties: {
              busPortability: { type: 'integer' }, waveformPortability: { type: 'integer' },
              schemaLeverage: { type: 'integer' }, roundTrip: { type: 'integer' },
              simplicity: { type: 'integer' }, fragmentFit: { type: 'integer' }, specFit: { type: 'integer' },
            },
          },
          total: { type: 'integer' },
          killerFlaw: { type: 'string' },
        },
      },
    },
    rationale: { type: 'string' },
    bestHybrid: { type: 'string', description: 'The strongest synthesis: which design is the spine and which ideas to graft from others' },
  },
}
const judgePrompts = [0,1,2].map(i =>
  `You are judge #${i+1} evaluating 4 designs that replace QProgram's legacy with_bus_mapping/with_waveforms.
Score each design 1-5 on: busPortability, waveformPortability (does it make waveforms portable per-bus/element — the user's explicit ask (a)), schemaLeverage (does it exploit BusRef/schema/naming — ask (b)), roundTrip (.qp serialization survival), simplicity, fragmentFit (coherence with the existing fragment/parameter system, avoiding two redundant parameterization systems), specFit (clean spec story). Sum to total.
Be adversarial: name each design's killerFlaw. The project is PRE-1.0 so ignore backward-compat; reward correctness and conceptual economy. Then rank, and propose the bestHybrid (which design is the spine + ideas to graft).
${i===0 ? 'Lens: prioritize CONCEPTUAL ECONOMY — fewest orthogonal concepts a user must learn.' : i===1 ? 'Lens: prioritize CORRECTNESS & EDGE CASES — raw-string buses, round-trip, per-element calibration, mixed schema/raw programs.' : 'Lens: prioritize REAL WORKFLOW — a physicist porting a calibrated experiment from q0 to q1 across two platforms.'}

DESIGNS:
${allDesigns}

Return the schema.`
)
const judgments = (await parallel(judgePrompts.map(p => () =>
  agent(p, { label: 'judge', phase: 'Judge', schema: JUDGE_SCHEMA })
))).filter(Boolean)

log(`Judge done: ${judgments.length} panels`)

// ---- Verify: stress the consensus-strongest ideas against concrete scenarios ----
phase('Verify')
const judgeBrief = judgments.map((j, i) =>
  `JUDGE ${i+1} ranking: ${j.ranking.map(r => `${r.name}=${r.total}(flaw:${r.killerFlaw})`).join(' | ')}\nHYBRID: ${j.bestHybrid}`
).join('\n\n')

const SCENARIOS = [
  'Port a fully-calibrated single-qubit Rabi experiment from q0 to q1, where q1 has DIFFERENT calibrated pulse amplitudes/durations than q0. The same alias "pi_pulse" must resolve to q1\'s concrete pulse.',
  'Move a program from platform A (naming "q0/drive") to platform B (naming "drive_q0_bus"), preserving all operations, then serialize to .qp and reload.',
  'A program that mixes schema-backed BusRefs (q[0].drive) AND raw-string buses ("aux_line"). Apply portability. What happens to the raw-string bus?',
  'A program with NO platform and NO concrete waveforms yet (just aliases) must still validate(), explain(), and serialize() — binding happens only at execute() time.',
]
const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['scenario', 'designName', 'passes', 'problem', 'fix'],
  properties: {
    scenario: { type: 'string' },
    designName: { type: 'string' },
    passes: { type: 'boolean' },
    problem: { type: 'string', description: 'Concrete failure or friction, or "none"' },
    fix: { type: 'string', description: 'Smallest change to the design that resolves it, or "n/a"' },
  },
}
const verifyResults = (await parallel(SCENARIOS.map(s => () =>
  agent(
    `Adversarially stress-test the WINNING/HYBRID design (per the judge panel below) against ONE concrete scenario. Walk it step by step. Does the design handle it cleanly? Find the friction or the hole; propose the smallest fix.

JUDGE PANEL CONSENSUS:
${judgeBrief}

CANDIDATE DESIGNS (for reference):
${allDesigns}

SCENARIO:
${s}

Return the schema. Set passes=false if there is ANY real friction; describe the problem precisely and the minimal fix.`,
    { label: 'verify', phase: 'Verify', schema: VERIFY_SCHEMA }
  )
))).filter(Boolean)

log(`Verify done: ${verifyResults.length} scenarios`)

// ---- Synthesize ----
phase('Synthesize')
const verifyBrief = verifyResults.map(v =>
  `SCENARIO: ${v.scenario}\n -> passes=${v.passes}; problem: ${v.problem}; fix: ${v.fix}`
).join('\n\n')

const finalDoc = await agent(
  `You are the lead architect. Write the FINAL design recommendation (markdown) for replacing QProgram's legacy with_bus_mapping/with_waveforms, for an expert reader (the QProgram author). Be decisive and concrete; pre-1.0 so propose the right design without backward-compat hedging.

The user's framing to answer directly:
(a) waveforms should ALSO be portable;
(b) with BusRef + schema naming templates, should bus mappings target schema elements — or are they needed at all?

INPUTS:
== CODEBASE FINDINGS ==
${findingsBrief}

== CANDIDATE DESIGNS ==
${allDesigns}

== JUDGE PANEL ==
${judgeBrief}

== ADVERSARIAL SCENARIO RESULTS ==
${verifyBrief}

Structure the doc:
1. **Verdict** (2-4 sentences): what to do with each of the two methods. Explicitly answer "are bus mappings needed at all?".
2. **The core reframe**: the one idea that unifies bus + waveform portability (cite the schema/BusRef/calibration facts that make it work).
3. **Recommended design**: concrete API (Python) + .qp implications + WHERE/WHEN binding happens + what re-validation runs. Include the per-element/bus waveform resolution mechanism that makes waveforms portable.
4. **What replaces with_bus_mapping** and **what replaces with_waveforms** — point by point against the legacy behavior, noting anything intentionally dropped.
5. **Edge cases & how they resolve** (raw-string buses, mixed programs, no-platform validate/serialize, cross-platform naming round-trip) — fold in the adversarial findings and their fixes.
6. **Migration & spec checklist** (code/tests/.specs/Notion per the project's DSL-change rule) — short.
7. **Open questions for the author** (max 3) — genuine decisions only.

Keep it tight and technical. No filler. Return ONLY the markdown.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return { finalDoc, findingsCount: findings.length, designs: designs.map(d => d.name), judgments, verifyResults }
