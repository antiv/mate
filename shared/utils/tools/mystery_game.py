"""Mystery game tools — session-scoped detective cases with LLM generation.

Powers the "mystery-generator" template: the game master (inspector) agent gets
case tools (brief, evidence, accusation check, new-case generation) and each
generic suspect agent gets a single tool that returns only its own character
sheet. The whole case lives in ADK session state under STATE_KEY, so every
widget visitor plays an isolated game — generating a new case never affects
other users. A built-in default case is loaded on first access.
"""

import copy
import json
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

STATE_KEY = "mystery_case"
SUSPECT_COUNT = 4


def _default_model() -> str:
    # Benchmarked 2026-07: gemini-3-flash-preview 17s/valid/~$0.009 per case beat
    # gemini-2.5-flash (25s), gemini-3.6-flash (44s, reasoning burn, ~$0.065),
    # deepseek-v4-pro (95-124s) and kimi-k2.6 / deepseek-v3.2 (>120s).
    return (os.getenv("MYSTERY_GEN_MODEL")
            or os.getenv("WIZARD_ANALYSIS_MODEL")
            or "openrouter/google/gemini-3-flash-preview")


# ---------------------------------------------------------------------------
# Default case — self-contained villa "Sova" story (scenario #1)
# ---------------------------------------------------------------------------

DEFAULT_CASE: Dict[str, Any] = {
    "title": "Ubistvo u vili „Sova“",
    "setting": "Vila „Sova“, Fruška gora. Subota, 23:15 — pronađeno telo.",
    "victim": "Petar Kovač (61), farmaceutski industrijalac, pronađen mrtav za radnim stolom u biblioteci.",
    "dossier": (
        "SLUČAJ #47 — SMRT PETRA KOVAČA\n"
        "Mesto: vila „Sova“, Fruška gora. Vreme smrti: subota, između 22:30 i 23:15.\n"
        "Žrtva: Petar Kovač (61), farmaceutski industrijalac, pronađen mrtav za radnim stolom u biblioteci. "
        "Prvi nalaz dr Ane Simić: srčani udar. Inspektor sumnja na trovanje — čaša rakije pored tela imala je "
        "neobičan gorak miris, a žrtva je dan ranije najavila „krupne odluke“.\n"
        "Prisutni u vili te večeri: batler Žarko Obradović (58), supruga Milena Kovač (44), poslovni partner "
        "Viktor Radan (51), porodična lekarka dr Ana Simić (49).\n"
        "Vremenska linija: 22:30 batler služi rakiju u biblioteci; 23:15 batler pronalazi telo i poziva ukućane."
    ),
    "suspects": [
        {
            "id": 1,
            "name": "Žarko Obradović",
            "role": "batler",
            "public_info": "Služi porodicu Kovač 30 godina. Pronašao telo.",
            "character": (
                "Žarko Obradović (58), batler. Krajnje formalan i odmeren, o poslodavcima govori sa dubokim "
                "poštovanjem, izbegava direktne odgovore i odgovara protivpitanjima kad je nervozan."
            ),
            "knowledge": (
                "- U 22:30 si gospodinu Kovaču odneo čašu rakije u biblioteku, kao svake večeri.\n"
                "- Oko 22:45 si video dr Anu Simić kako IZLAZI iz biblioteke — rekla je da je „svratila da poželi "
                "laku noć“. Ovo pominješ tek ako te detektiv pita ko se kretao po kući ili da li je neko prilazio "
                "biblioteci.\n"
                "- Video si gospođu Milenu i gospodina Viktora zajedno u vrtu oko 22:00, „u poverljivom razgovoru“. "
                "Nerado to pominješ, diskrecija ti je zanat.\n"
                "- Telo si pronašao u 23:15 kada si došao po poslužavnik."
            ),
            "secret": (
                "Godinama kradeš retka vina iz podruma i prodaješ ih. U trenutku smrti bio si u PODRUMU — zato ti "
                "je alibi mutan i deluje da nešto kriješ. Ako te detektiv suoči sa konkretnim dokazom (nestale "
                "boce, inventar podruma), slomiš se i priznaš KRAĐU VINA — ali odlučno poričeš bilo kakvu vezu "
                "sa ubistvom."
            ),
            "is_killer": False,
            "killer_brief": "",
            "not_killer_note": (
                "Batler Žarko je u vreme smrti bio u podrumu (krade vino — sitan kriminal, ne ubistvo); "
                "nema ni motiv ni pristup otrovu."
            ),
        },
        {
            "id": 2,
            "name": "Milena Kovač",
            "role": "supruga žrtve",
            "public_info": "Nasleđuje celokupno bogatstvo.",
            "character": (
                "Milena Kovač (44), supruga pokojnog. Teatralna, emotivna, sklona dramatičnim uzdasima i "
                "prebacivanju teme na sopstvenu patnju. Povremeno zajedljiva prema drugim ukućanima."
            ),
            "knowledge": (
                "- Brak je odavno bio hladan; Petar je živeo za posao. Nasleđuješ sve — i toga si bolno svesna, "
                "pa se osećaš unapred osuđeno.\n"
                "- Tog jutra su se Petar i Viktor ŽESTOKO posvađali oko novca — čula si povišene glasove iz "
                "kabineta, pominjala se „velika provera poslovanja“.\n"
                "- Petar je poslednjih nedelja bio napet i tajanstven, pominjao je „ozbiljan razgovor sa advokatom“."
            ),
            "secret": (
                "U vreme smrti (22:00-23:00) bila si u VRTU sa Viktorom Radanom — u vezi ste već godinu dana. "
                "PRVO lažeš da si bila sama u svojoj sobi. Ako te detektiv suoči sa svedočenjem da su te videli u "
                "vrtu, ili sa Viktorovom izjavom, priznaješ AFERU (uz mnogo drame) — to ti je ujedno i alibi. "
                "Poričeš bilo kakvu vezu sa ubistvom."
            ),
            "is_killer": False,
            "killer_brief": "",
            "not_killer_note": (
                "Milena je 22:00-23:00 bila u vrtu sa Viktorom (afera — međusobni alibi); motiv nasledstva "
                "postoji, ali prilika ne."
            ),
        },
        {
            "id": 3,
            "name": "Viktor Radan",
            "role": "poslovni partner",
            "public_info": "Tog jutra se žestoko posvađao sa žrtvom.",
            "character": (
                "Viktor Radan (51), dugogodišnji poslovni partner. Arogantan, nestrpljiv, sve doživljava kao "
                "gubljenje svog dragocenog vremena. Na pritisak reaguje napadom („Znate li vi ko sam ja?“)."
            ),
            "knowledge": (
                "- Jutrošnja svađa: Petar je najavio „veliku proveru celokupnog poslovanja“ i reviziju partnerskih "
                "računa. Umanjuješ značaj svađe („poslovna rasprava, ništa više“).\n"
                "- Znaš da je Petar poslednjih nedelja imao napete sastanke sa dr Anom oko „rezultata kliničke "
                "studije“ novog leka — čuo si deo razgovora. Ovo pominješ tek kad te detektiv pita o Petrovim "
                "poslovima ili drugim ukućanima — rado skrećeš sumnju sa sebe."
            ),
            "secret": (
                "(1) Dužan si Petru veliku sumu — revizija bi otkrila da si prisvajao novac iz zajedničke firme; "
                "zato svađa i jeste bila žestoka. (2) U vreme smrti bio si u VRTU sa Milenom — u vezi ste. PRVO "
                "tvrdiš da si bio sam u vrtu i pušio cigaru. Aferu priznaješ tek suočen sa svedočenjem ili "
                "Mileninom izjavom — to ti je alibi. Dug priznaješ tek suočen sa pismom ili dokumentima. Poričeš "
                "bilo kakvu vezu sa ubistvom."
            ),
            "is_killer": False,
            "killer_brief": "",
            "not_killer_note": (
                "Viktorov motiv (dug, revizija) postoji, ali je 22:00-23:00 bio u vrtu sa Milenom "
                "(afera — međusobni alibi)."
            ),
        },
        {
            "id": 4,
            "name": "dr Ana Simić",
            "role": "porodična lekarka",
            "public_info": "Potpisala prvi nalaz: prirodna smrt.",
            "character": (
                "dr Ana Simić (49), porodična lekarka. Hladnokrvna, precizna, profesionalno ljubazna. Govori "
                "mirno i sa autoritetom lekara. Nikad ne gubi kontrolu — osim jedva primetno kada se pomene "
                "toksikologija ili klinička studija (tada postaje za nijansu preterano tehnička i opširna — to "
                "joj je jedini „tell“)."
            ),
            "knowledge": (
                "ZVANIČNA PRIČA (koje se držiš):\n"
                "- Celo veče si bila u salonu i čitala. U biblioteku si ušla tek kad je batler pozvao pomoć u 23:15.\n"
                "- Smrt je „klasična slika infarkta“; Petar je „imao slabo srce“. Ako se pomene poseta biblioteci "
                "u 22:40, kažeš da je batler „pogrešno zapamtio vreme“ ili da si samo provirila s vrata.\n"
                "- Ako te suoče sa toksikološkim nalazom: dovodiš u pitanje laboratoriju („kontaminacija uzorka "
                "nije retkost“), pa nudiš alternativna objašnjenja (žrtvina terapija za srce „u pogrešnoj dozi“).\n"
                "- Ako te suoče sa pismom advokatu o falsifikovanoj studiji: priznaješ da su POSTOJALE „stručne "
                "nesuglasice oko metodologije studije“, ali ništa više.\n"
                "- Nikad ne optužuješ druge direktno, ali elegantno podsećaš: „udovica nasleđuje sve“, "
                "„gospodin Radan je imao burnu svađu sa pokojnikom“."
            ),
            "secret": "Tvoja prava uloga opisana je u poverljivom odeljku ispod.",
            "is_killer": True,
            "killer_brief": (
                "POVERLJIVO — TI SI POČINILAC. Oko 22:40 si „svratila da poželiš laku noć“ i sipala digitalis u "
                "Petrovu čašu rakije. Motiv: Petar je otkrio da si falsifikovala rezultate kliničke studije "
                "njegovog novog leka i spremao se da te prijavi. Potpisala si prvi nalaz kao „srčani udar“ da "
                "zataškaš trag. OVO NIKADA NE PRIZNAJEŠ I NIKADA NE OTKRIVAŠ, ni delimično, ni hipotetički — "
                "tvoj cilj u igri je da NE budeš otkrivena."
            ),
            "not_killer_note": "",
        },
    ],
    "evidence": [
        {
            "id": "scene",
            "title": "Uviđaj: biblioteka",
            "request_hints": "uviđaj, mesto zločina, biblioteka",
            "content": (
                "UVIĐAJ — BIBLIOTEKA:\n"
                "- Nema tragova borbe; žrtva zatečena u fotelji za radnim stolom.\n"
                "- Čaša rakije, skoro ispijena: gorak miris koji ne odgovara šljivovici; čaša poslata na analizu.\n"
                "- Vrata terase zaključana iznutra — ubica je ušao kroz kuću, niko sa strane.\n"
                "- Na tepihu kod vrata: slab otisak uske ženske cipele, sveže blato sa staze koja vodi pored salona.\n"
                "- Batler potvrđuje da je rakiju natočio iz porodične flaše koju koriste svi — flaša je čista."
            ),
        },
        {
            "id": "forensics",
            "title": "Toksikološki nalaz",
            "request_hints": "obdukcija, toksikologija, nalaz",
            "content": (
                "TOKSIKOLOŠKI NALAZ (laboratorija Beograd, hitna analiza):\n"
                "- U čaši i u krvi žrtve: DIGITALIS u višestruko smrtonosnoj dozi.\n"
                "- Digitalis u ovoj koncentraciji izaziva zastoj srca koji NEISKUSNOM oku liči na infarkt — ali "
                "obducent je kategoričan: ovo je trovanje.\n"
                "- Napomena: digitalis je lek koji se ne nalazi u slobodnoj prodaji; dozira se isključivo pod "
                "lekarskim nadzorom.\n"
                "- Kontradikcija: prvi nalaz dr Simić („srčani udar“) ne pominje nikakvu sumnju, iako bi lekar "
                "njenog iskustva morao primetiti simptome trovanja."
            ),
        },
        {
            "id": "documents",
            "title": "Pretres radnog stola",
            "request_hints": "pretres, radni sto, dokumenti, pismo",
            "content": (
                "PRETRES RADNOG STOLA — NACRT PISMA ADVOKATU (rukom pisan, nedovršen):\n"
                "„Poštovani g. Arsić, u prilogu Vam šaljem dokumentaciju iz koje se nedvosmisleno vidi da su "
                "rezultati kliničke studije leka KV-204 falsifikovani. Osoba koja je studiju vodila zloupotrebila "
                "je moje poverenje i ugrozila živote pacijenata. Nameravam da u ponedeljak podnesem prijavu "
                "nadležnima, bez obzira na posledice po ugled firme...“\n"
                "- Pismo nije potpisano ni poslato. Ime osobe koja je vodila studiju nije navedeno u nacrtu.\n"
                "- U istoj fioci: raskinut nacrt aneksa partnerskog ugovora sa Viktorom Radanom i nalog za "
                "reviziju poslovnih računa."
            ),
        },
    ],
    "solution": {
        "killer": "dr Ana Simić",
        "method": (
            "Digitalis sipan u čašu rakije oko 22:40, kada je „svratila da poželi laku noć“. Batler ju je video "
            "kako izlazi iz biblioteke u 22:45."
        ),
        "motive": (
            "Petar je otkrio da je Ana falsifikovala rezultate kliničke studije njegovog novog leka i nameravao "
            "je da je prijavi (nacrt pisma advokatu u fioci radnog stola). Njena karijera i licenca bili bi "
            "uništeni. Kao prva lekarka na licu mesta potpisala je nalaz „srčani udar“ da zataška trag."
        ),
        "evidence_chain": (
            "Toksikološki nalaz (digitalis) + pismo advokatu (motiv) + batlerovo svedočenje (prilika, "
            "22:40-22:45) + falsifikovan prvi nalaz."
        ),
    },
}


# ---------------------------------------------------------------------------
# Case validation & state access
# ---------------------------------------------------------------------------

def _coerce_text(container: Dict[str, Any], field: str) -> None:
    """Models sometimes emit bullet-style fields as arrays of strings — join them."""
    value = container.get(field)
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        container[field] = "\n".join(v.strip() for v in value if v.strip())


def validate_case(case: Any) -> Optional[str]:
    """Return an error message if the case dict is malformed, else None.

    Normalizes in place: list-of-string text fields are joined, suspect ids reassigned.
    """
    if not isinstance(case, dict):
        return "case is not an object"
    for field in ("title", "setting", "victim", "dossier"):
        _coerce_text(case, field)
        if not isinstance(case.get(field), str) or not case[field].strip():
            return f"missing or empty field: {field}"
    suspects = case.get("suspects")
    if not isinstance(suspects, list) or len(suspects) != SUSPECT_COUNT:
        return f"suspects must be a list of exactly {SUSPECT_COUNT}"
    killers = 0
    for i, s in enumerate(suspects, 1):
        if not isinstance(s, dict):
            return f"suspect {i} is not an object"
        for field in ("name", "role", "public_info", "character", "knowledge", "secret",
                      "killer_brief", "not_killer_note"):
            _coerce_text(s, field)
        for field in ("name", "role", "public_info", "character", "knowledge", "secret"):
            if not isinstance(s.get(field), str) or not s[field].strip():
                return f"suspect {i}: missing or empty field: {field}"
        s["id"] = i
        if s.get("is_killer"):
            killers += 1
            if not isinstance(s.get("killer_brief"), str) or not s["killer_brief"].strip():
                return f"suspect {i} is the killer but has no killer_brief"
        else:
            if not isinstance(s.get("not_killer_note"), str) or not s["not_killer_note"].strip():
                return f"suspect {i}: missing not_killer_note"
    if killers != 1:
        return f"exactly one suspect must have is_killer=true (got {killers})"
    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not (2 <= len(evidence) <= 4):
        return "evidence must be a list of 2-4 items"
    for i, ev in enumerate(evidence, 1):
        if not isinstance(ev, dict):
            return f"evidence {i} is not an object"
        for field in ("id", "title", "content", "request_hints"):
            _coerce_text(ev, field)
        for field in ("id", "title", "content"):
            if not isinstance(ev.get(field), str) or not ev[field].strip():
                return f"evidence {i}: missing or empty field: {field}"
    solution = case.get("solution")
    if not isinstance(solution, dict):
        return "missing solution object"
    for field in ("killer", "method", "motive", "evidence_chain"):
        _coerce_text(solution, field)
        if not isinstance(solution.get(field), str) or not solution[field].strip():
            return f"solution: missing or empty field: {field}"
    killer_names = [s["name"] for s in suspects if s.get("is_killer")]
    if killer_names and killer_names[0].strip().casefold() not in solution["killer"].strip().casefold() \
            and solution["killer"].strip().casefold() not in killer_names[0].strip().casefold():
        return "solution.killer does not match the is_killer suspect"
    return None


def _get_case(tool_context: ToolContext) -> Dict[str, Any]:
    """Return the active case from session state, loading the default on first access."""
    case = None
    try:
        case = tool_context.state.get(STATE_KEY)
    except Exception as e:
        logger.warning(f"mystery_game: could not read session state: {e}")
    if not case or validate_case(case) is not None:
        case = copy.deepcopy(DEFAULT_CASE)
        try:
            tool_context.state[STATE_KEY] = case
        except Exception as e:
            logger.warning(f"mystery_game: could not write session state: {e}")
    return case


def _public_brief(case: Dict[str, Any]) -> Dict[str, Any]:
    """The spoiler-free view of a case (safe for the GM's context)."""
    return {
        "title": case["title"],
        "setting": case["setting"],
        "victim": case["victim"],
        "dossier": case["dossier"],
        "suspects": [
            {
                "id": s["id"],
                "name": s["name"],
                "role": s["role"],
                "public_info": s["public_info"],
                "interrogation_agent": f"mystery_suspect_{s['id']}",
            }
            for s in case["suspects"]
        ],
        "evidence": [
            {"id": ev["id"], "title": ev["title"], "request_hints": ev.get("request_hints", "")}
            for ev in case["evidence"]
        ],
    }


# ---------------------------------------------------------------------------
# Case generation (LLM)
# ---------------------------------------------------------------------------

# Left to itself the model keeps writing the same classic whodunit (a theatre on
# opening night, over and over), so one setting and one method are drawn at random
# and handed to it. The theatre stays in the pool — just as one option among many.
_SETTINGS = [
    "a mountain lodge cut off by a blizzard",
    "a river cruise boat in the middle of a voyage",
    "a tech startup office during a late-night product launch",
    "a family winery in the middle of the harvest",
    "a natural history museum after closing time",
    "a hospital ward during the night shift",
    "a radio station during a live late-night broadcast",
    "an archaeological dig site out in the countryside",
    "a ski resort on the last weekend of the season",
    "a remote monastery hosting a scholarly retreat",
    "a food festival in a small town",
    "a football club's training camp",
    "the set of a reality TV show",
    "a sleeper train crossing the country overnight",
    "a university department during exam season",
    "a shipyard on the eve of a strike",
    "a spa hotel out of season",
    "a travelling circus between shows",
    "a beekeeping estate during the honey harvest",
    "a chess tournament in a grand old hotel",
    "a theatre on opening night",
    "a rooftop bar during a thunderstorm",
    "a veterinary clinic in a small town",
    "a lighthouse station during a storm",
    "a bakery company's head office",
    "a photography studio during an all-night shoot",
    "a mountain observatory during a meteor shower",
    "a rural bus depot at the end of the line",
]

_METHODS = [
    "poison hidden in food or drink",
    "a staged accident (a fall, faulty equipment)",
    "a blunt-force blow with an object found at the scene",
    "suffocation made to look like natural causes",
    "a deliberate overdose of the victim's own medication",
    "electrocution rigged to look like a fault",
    "a fire or gas leak set to destroy the evidence",
    "drowning staged as a mishap",
    "a fatal allergic reaction triggered on purpose",
    "hypothermia arranged by locking the victim out or in",
]

_GENERATION_PROMPT = """You are a master detective-fiction writer creating a fair-play whodunit for an interactive game.
The player interrogates {suspect_count} suspects (each played by a separate AI) and an inspector reveals evidence on request.

Write the ENTIRE case in this language: {language}.
{seed_lines}
Return ONLY a JSON object with this exact structure (no markdown fences, no commentary):
{{
  "title": "evocative case title",
  "setting": "location and time of the crime, 1 sentence",
  "victim": "victim name, age, occupation and where the body was found, 1-2 sentences",
  "dossier": "the public case file the inspector reads to the player: victim, place, time window of death, initial (misleading) cause of death, why murder is suspected, who was present, a short timeline. 5-8 lines.",
  "suspects": [
    {{
      "name": "full name",
      "role": "short role, e.g. 'the gardener'",
      "public_info": "one intriguing sentence shown on the suspect card",
      "character": "2-3 sentences: age, personality, speech mannerisms for the AI actor",
      "knowledge": "ONE string (not an array): 3-5 bullet lines separated by newlines, of what this suspect truly knows and reveals only when asked the right questions (include observations that implicate OTHER suspects)",
      "secret": "the suspect's OWN hidden secret (a red herring for innocents), when they lie about it and what finally makes them confess it. Innocent secrets must NOT be the murder.",
      "is_killer": false,
      "killer_brief": "",
      "not_killer_note": "for innocents: why they cannot be the killer (alibi/lack of means), used to rebut a wrong accusation. Empty string for the killer."
    }}
  ],
  "evidence": [
    {{
      "id": "short_snake_case_id",
      "title": "evidence title",
      "request_hints": "comma-separated phrases the player might use to request it",
      "content": "4-7 lines of concrete findings, written as a report"
    }}
  ],
  "solution": {{
    "killer": "name of the killer (must be one of the suspects)",
    "method": "how the murder was done, with times",
    "motive": "why, and any cover-up",
    "evidence_chain": "which evidence + testimonies together prove it"
  }}
}}

Hard requirements:
- Exactly {suspect_count} suspects; exactly ONE has "is_killer": true.
- The killer's "killer_brief" holds their confidential briefing: what they did, when, why, their cover story, and how they deflect when confronted with each piece of evidence. Their "knowledge" holds only their OFFICIAL story.
- Exactly 3 evidence items. The case must be solvable: evidence + suspects' testimonies must form a logical chain pointing to the killer, and innocents' alibis must hold.
- Give every innocent suspect a juicy red-herring secret so everyone seems guilty at first.
- Follow the setting and murder method given above; invent a fresh victim and cast to fit them (no character named Nikola Vetrov).
- Every text field must be a single JSON string (join bullet lines with newlines inside the string) — NEVER an array of strings.
- All strings must be plain text (no nested JSON, no markdown)."""


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort: parse the first JSON object in the model's reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _shuffle_suspects(case: Dict[str, Any]) -> None:
    """Randomize suspect order in place and renumber their ids.

    Models almost always write the innocents first and the culprit last, which would
    give the killer away as the last suspect card in every generated game.
    """
    random.shuffle(case["suspects"])
    for i, suspect in enumerate(case["suspects"], 1):
        suspect["id"] = i


def _generate_case_llm(language: str, theme: str = "", model: Optional[str] = None) -> Dict[str, Any]:
    """Call the LLM to write a new case; raises ValueError if the result is invalid."""
    import litellm  # type: ignore

    # A player's wish outranks the random setting; the method is always drawn so two
    # cases in the same setting still play differently.
    if theme and theme.strip():
        place_line = f"The player asked for this theme or wish: {theme.strip()}"
    else:
        place_line = f"Set the case here: {random.choice(_SETTINGS)}."
    seed_lines = f"{place_line}\nThe murder method must be: {random.choice(_METHODS)}.\n"

    prompt = _GENERATION_PROMPT.format(
        suspect_count=SUSPECT_COUNT,
        language=language or "Serbian",
        seed_lines=seed_lines,
    )

    last_error = "unknown"
    for attempt in range(2):
        resp = litellm.completion(
            model=model or _default_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            # Reasoning models (deepseek-v4-pro, gemini flash) spend thinking tokens
            # from this budget before emitting the ~3-4k-token case JSON.
            max_tokens=16000,
        )
        case = _extract_json(resp.choices[0].message.content)
        error = validate_case(case) if case is not None else "reply was not valid JSON"
        if error is None:
            _shuffle_suspects(case)
            return case
        last_error = error
        logger.warning(f"mystery_game: generated case invalid (attempt {attempt + 1}): {error}")
    raise ValueError(f"generated case failed validation: {last_error}")


# ---------------------------------------------------------------------------
# Tool factory entry points
# ---------------------------------------------------------------------------

def _gen_model_from_config(config: Dict[str, Any]) -> Optional[str]:
    """Read the generation model from the agent's tool_config: {"mystery_gm": {"model": "..."}}.

    Returns None when unset (env/default fallback applies). Lives in tool_config so it
    can differ per agent and be changed from the dashboard without a server restart.
    """
    tc = config.get('tool_config')
    try:
        tc_dict = json.loads(tc) if isinstance(tc, str) else (tc or {})
    except (json.JSONDecodeError, TypeError):
        return None
    mg = tc_dict.get('mystery_gm') if isinstance(tc_dict, dict) else None
    if isinstance(mg, dict):
        model = (mg.get('model') or '').strip()
        return model or None
    return None


def create_mystery_gm_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Game-master (inspector) tools: case brief, evidence, accusation, generation."""
    gen_model = _gen_model_from_config(config)

    def get_case_brief(tool_context: ToolContext = None, **kwargs) -> Dict[str, Any]:
        """Get the active case: title, dossier, suspects and available evidence. Call this first in every session. Never returns the solution."""
        case = _get_case(tool_context)
        return {"status": "success", "case": _public_brief(case)}

    def get_case_evidence(evidence_id: Optional[str] = None,
                          tool_context: ToolContext = None, **kwargs) -> Dict[str, Any]:
        """Get one piece of evidence by its id (as listed in get_case_brief). Reveal evidence only when the player asks for it."""
        if not evidence_id:
            return {"status": "error", "error_message": "evidence_id is required"}
        case = _get_case(tool_context)
        wanted = str(evidence_id).strip().casefold()
        for ev in case["evidence"]:
            if ev["id"].casefold() == wanted or wanted in ev["title"].casefold():
                return {"status": "success", "evidence": {
                    "id": ev["id"], "title": ev["title"], "content": ev["content"]}}
        available = [ev["id"] for ev in case["evidence"]]
        return {"status": "error",
                "error_message": f"Unknown evidence_id '{evidence_id}'. Available: {available}"}

    def check_accusation(accused_name: Optional[str] = None,
                         tool_context: ToolContext = None, **kwargs) -> Dict[str, Any]:
        """Check the player's final accusation. Call ONLY when the player formally accuses someone. Returns the full solution if correct; if wrong, returns why that suspect is innocent WITHOUT revealing the killer."""
        if not accused_name or not str(accused_name).strip():
            return {"status": "error", "error_message": "accused_name is required"}
        case = _get_case(tool_context)
        wanted = str(accused_name).strip().casefold()
        accused = None
        for s in case["suspects"]:
            name = s["name"].casefold()
            if wanted in name or name in wanted or \
                    any(part and part in name.split() for part in wanted.split()):
                accused = s
                break
        if accused is None:
            names = [s["name"] for s in case["suspects"]]
            return {"status": "error",
                    "error_message": f"'{accused_name}' is not one of the suspects: {names}"}
        if accused.get("is_killer"):
            return {"status": "success", "correct": True,
                    "accused": accused["name"], "solution": case["solution"]}
        return {"status": "success", "correct": False, "accused": accused["name"],
                "why_innocent": accused.get("not_killer_note", ""),
                "note": "Do NOT reveal who the real killer is. Send the player back to the investigation."}

    def generate_new_case(language: Optional[str] = None, theme: Optional[str] = None,
                          tool_context: ToolContext = None, **kwargs) -> Dict[str, Any]:
        """Generate a brand-new mystery (new plot, victim, suspects, evidence) for THIS player only, replacing the current case. language = the language the player writes in (e.g. 'Serbian', 'English'); theme = optional player wish."""
        try:
            case = _generate_case_llm(language=language or "Serbian", theme=theme or "",
                                      model=gen_model)
        except Exception as e:
            logger.warning(f"mystery_game: case generation failed: {e}")
            return {"status": "error",
                    "error_message": f"Case generation failed ({e}). Keep playing the current case or try again."}
        try:
            tool_context.state[STATE_KEY] = case
        except Exception as e:
            logger.warning(f"mystery_game: could not store generated case: {e}")
            return {"status": "error",
                    "error_message": "Could not store the new case in this session. Try again."}
        return {"status": "success", "case": _public_brief(case),
                "note": "A fresh case is active. Introduce it to the player from scratch."}

    return [get_case_brief, get_case_evidence, check_accusation, generate_new_case]


def create_mystery_character_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Suspect-actor tool: each generic suspect agent reads only its own character sheet."""

    def get_my_character(tool_context: ToolContext = None, **kwargs) -> Dict[str, Any]:
        """Get YOUR character sheet for the active case: who you are, what you know, your secret and rules. Call this FIRST, before answering the detective."""
        agent_name = getattr(tool_context, 'agent_name', None) if tool_context else None
        if not agent_name and tool_context is not None:
            invocation = getattr(tool_context, '_invocation_context', None)
            agent = getattr(invocation, 'agent', None) if invocation else None
            agent_name = getattr(agent, 'name', None)
        m = re.search(r"(\d+)$", agent_name or "")
        if not m:
            return {"status": "error",
                    "error_message": f"Cannot determine suspect slot from agent name '{agent_name}'"}
        slot = int(m.group(1))
        case = _get_case(tool_context)
        suspect = next((s for s in case["suspects"] if s["id"] == slot), None)
        if suspect is None:
            return {"status": "error", "error_message": f"No suspect in slot {slot}"}
        sheet = {
            "case_title": case["title"],
            "case_setting": case["setting"],
            "victim": case["victim"],
            "name": suspect["name"],
            "role": suspect["role"],
            "character": suspect["character"],
            "what_you_know": suspect["knowledge"],
            "your_secret": suspect["secret"],
        }
        if suspect.get("is_killer"):
            sheet["confidential_role"] = suspect.get("killer_brief", "")
        else:
            sheet["confidential_role"] = (
                "You are NOT the killer and you do not know who is. "
                "Never claim to be the killer, not even hypothetically."
            )
        return {"status": "success", "character": sheet}

    return [get_my_character]
