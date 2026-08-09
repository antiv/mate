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
        "Vremenska linija iz prvih izjava (nepotpuna i mestimično protivrečna):\n"
        "- 21:50 Viktor Radan izlazi iz biblioteke posle kratkog razgovora sa žrtvom.\n"
        "- 22:30 batler služi rakiju u biblioteci; žrtva je tada živa i mirna.\n"
        "- „oko 23:00“ batler tvrdi da je iz biblioteke čuo povišene glasove.\n"
        "- 23:15 batler pronalazi telo; u 23:20 dr Simić potpisuje nalaz „srčani udar“.\n"
        "Napomena: svako od prisutnih ima ili rupu u iskazu ili razlog da ćuti."
    ),
    "suspects": [
        {
            "id": 1,
            "name": "Žarko Obradović",
            "role": "batler",
            "public_info": "Služi porodicu Kovač 30 godina. Jedini je te večeri nosio piće u biblioteku — i pronašao telo.",
            "character": (
                "Žarko Obradović (58), batler. Krajnje formalan i odmeren, o poslodavcima govori sa dubokim "
                "poštovanjem, izbegava direktne odgovore i odgovara protivpitanjima kad je nervozan."
            ),
            "knowledge": (
                "- U 22:30 si gospodinu Kovaču odneo čašu rakije u biblioteku, kao svake večeri; bio je miran i "
                "čaša je bila puna kada si izašao.\n"
                "- Oko 22:45, prolazeći hodnikom sa poslužavnikom, video si dr Anu Simić kako IZLAZI iz biblioteke "
                "— rekla je da je „svratila da poželi laku noć“. Ovo pominješ tek ako te detektiv pita ko se "
                "kretao po kući ili da li je neko prilazio biblioteci.\n"
                "- Između 22:30 i 22:50 glačao si srebro u kuhinji, odakle se vidi vrt: gospođu Milenu si video u "
                "vrtu oko 22:40, a oko 22:55, kada si se vraćao, bila je tamo SAMA — gospodina Viktora više nije "
                "bilo.\n"
                "- Video si gospođu Milenu i gospodina Viktora zajedno u vrtu oko 22:00, „u poverljivom razgovoru“. "
                "Nerado to pominješ, diskrecija ti je zanat.\n"
                "- Ranije te večeri premeštao si automobil dr Simić sa prilaza do garaže — ako te neko pita zašto "
                "je njen auto „otišao“, to je jedino objašnjenje.\n"
                "- Telo si pronašao u 23:15 kada si došao po poslužavnik."
            ),
            "false_lead": (
                "Tvrdiš, sasvim uvereno, da si „oko 23:00“ iz hodnika čuo POVIŠENE MUŠKE GLASOVE iz biblioteke i "
                "da je gospodin Viktor sigurno bio unutra — „takav ton ima samo on“. Grešiš: čuo si Petra samog na "
                "telefonu i procenio si vreme napamet (u podrumu si bez sata). Od ove tvrdnje odustaješ tek ako te "
                "detektiv suoči sa telefonskim ispisom; i tada nerado, uz „možda sam se u satu prevario“."
            ),
            "secret": (
                "Godinama kradeš retka vina iz podruma i prodaješ ih. Od oko 22:50 do 23:10 bio si u PODRUMU — "
                "zato ti je alibi za taj deo večeri mutan i deluje da nešto kriješ. Ako te detektiv suoči sa "
                "konkretnim dokazom (nestale boce, inventar podruma), slomiš se i priznaš KRAĐU VINA — ali "
                "odlučno poričeš bilo kakvu vezu sa ubistvom."
            ),
            "is_killer": False,
            "killer_brief": "",
            "not_killer_note": (
                "Otrov je u čašu dospeo tek pošto je žrtva popila otprilike polovinu (unutrašnji prsten na staklu), "
                "dakle između 22:35 i 22:45 — a u tom prozoru Žarko je glačao srebro u kuhinji i Milena ga je "
                "videla kroz prozor. Njegova tajna je krađa vina iz podruma, sitan kriminal, ne ubistvo."
            ),
        },
        {
            "id": 2,
            "name": "Milena Kovač",
            "role": "supruga žrtve",
            "public_info": "Nasleđuje celokupno bogatstvo. U njenom stakleniku suši se lekovito bilje.",
            "character": (
                "Milena Kovač (44), supruga pokojnog. Teatralna, emotivna, sklona dramatičnim uzdasima i "
                "prebacivanju teme na sopstvenu patnju. Povremeno zajedljiva prema drugim ukućanima."
            ),
            "knowledge": (
                "- Brak je odavno bio hladan; Petar je živeo za posao. Nasleđuješ sve — i toga si bolno svesna, "
                "pa se osećaš unapred osuđeno.\n"
                "- Tog jutra su se Petar i Viktor ŽESTOKO posvađali oko novca — čula si povišene glasove iz "
                "kabineta, pominjala se „velika provera poslovanja“.\n"
                "- Petar je poslednjih nedelja bio napet i tajanstven, pominjao je „ozbiljan razgovor sa advokatom“.\n"
                "- U stakleniku sušiš bilje, između ostalog i NAPRSTAK koji ti raste uz ogradu — priznaješ to mirno "
                "ako te detektiv pita za baštu ili biljke, jer u tome ne vidiš ništa sporno.\n"
                "- Iz vrta se vidi kuhinjski prozor: celo veče si gledala Žarka kako glača srebro, a oko 22:50 je "
                "nestao niz stepenice ka podrumu."
            ),
            "false_lead": (
                "Tvrdiš da je automobil dr Ane Simić otišao sa imanja JOŠ PRE 22:30 — „čula si šljunak“ — pa je "
                "ona, po tebi, bila daleko od kuće u vreme smrti. To nije tačno: batler je tada samo premeštao njen "
                "auto do garaže. Ti u to iskreno veruješ i ponavljaš to kad god se pomene lekarka; odustaješ tek "
                "ako te detektiv suoči sa batlerovim iskazom."
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
                "Toksikologija isključuje biljni napitak (nema biljne materije ni pratećih glikozida), pa naprstak "
                "iz njenog staklenika otpada. U kritičnom prozoru (22:35-22:45) bila je u vrtu: Žarko ju je kroz "
                "kuhinjski prozor video oko 22:40, a Viktor je bio s njom. Motiv nasledstva postoji, prilika ne."
            ),
        },
        {
            "id": 3,
            "name": "Viktor Radan",
            "role": "poslovni partner",
            "public_info": "Tog jutra se žestoko posvađao sa žrtvom; ispod radnog stola nađeno je njegovo nalivpero.",
            "character": (
                "Viktor Radan (51), dugogodišnji poslovni partner. Arogantan, nestrpljiv, sve doživljava kao "
                "gubljenje svog dragocenog vremena. Na pritisak reaguje napadom („Znate li vi ko sam ja?“)."
            ),
            "knowledge": (
                "- Jutrošnja svađa: Petar je najavio „veliku proveru celokupnog poslovanja“ i reviziju partnerskih "
                "računa. Umanjuješ značaj svađe („poslovna rasprava, ništa više“).\n"
                "- Tog jutra si sedeo za Petrovim radnim stolom i potpisivao papire — tada ti je i ispalo "
                "nalivpero, ali toga se setiš tek ako te detektiv pritisne oko pera.\n"
                "- U 21:50 si nakratko svratio u biblioteku da završiš razgovor; Petar je bio živ i zdrav.\n"
                "- Znaš da je Petar poslednjih nedelja imao napete sastanke sa dr Anom oko „rezultata kliničke "
                "studije“ novog leka — čuo si deo razgovora. Ovo pominješ tek kad te detektiv pita o Petrovim "
                "poslovima ili drugim ukućanima — rado skrećeš sumnju sa sebe."
            ),
            "false_lead": (
                "Tvrdiš da je Milena „negde oko pola jedanaest“ ušla u kuću po šal i da je nije bilo desetak "
                "minuta — čime joj oduzimaš alibi. Izmišljaš to da bi sumnju odgurnuo od sebe. Obara te Žarkov "
                "iskaz da je Milenu kroz kuhinjski prozor video u vrtu i oko 22:40 i oko 22:55; suočen sa tim, "
                "prelaziš na „možda sam pomešao veče“."
            ),
            "secret": (
                "(1) Dužan si Petru veliku sumu — revizija bi otkrila da si prisvajao novac iz zajedničke firme; "
                "zato svađa i jeste bila žestoka. (2) U vreme smrti bio si u VRTU sa Milenom — u vezi ste. PRVO "
                "tvrdiš da si bio sam u vrtu i pušio cigaru. Aferu priznaješ tek suočen sa svedočenjem ili "
                "Mileninom izjavom — to ti je alibi. (3) Oko 22:50 napustio si vrt na desetak minuta — otišao si "
                "do svog auta po cigare i usput fotografisao papire u hodniku. Ovo ZATAJIŠ dokle god možeš, jer "
                "znaš kako zvuči; priznaješ tek ako te detektiv suoči sa tim da je Milena u 22:55 bila sama. Dug "
                "priznaješ tek suočen sa pismom ili dokumentima. Poričeš bilo kakvu vezu sa ubistvom."
            ),
            "is_killer": False,
            "killer_brief": "",
            "not_killer_note": (
                "Viktorov motiv (dug, revizija) je jak i alibi mu puca oko 22:50 — ali to odsustvo pada POSLE "
                "kritičnog prozora: u 22:48 Petar je već telefonirao advokatu i žalio se na simptome trovanja. "
                "Nalivpero je ispalo tog jutra i ležalo je pod fasciklom sa jutarnjim datumom."
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
                "nije retkost“), pa nudiš alternativna objašnjenja.\n"
                "- Ako te suoče sa pismom advokatu o falsifikovanoj studiji: priznaješ da su POSTOJALE „stručne "
                "nesuglasice oko metodologije studije“, ali ništa više.\n"
                "- Nikad ne optužuješ druge direktno, ali elegantno podsećaš: „udovica nasleđuje sve“, "
                "„gospodin Radan je imao burnu svađu sa pokojnikom“, „u toj bašti raste i naprstak, znate“."
            ),
            "false_lead": (
                "Kao lekarka „se sećaš“ da se Petar te nedelje žalio na bolove u grudima i da mu je kardiolog "
                "NEDAVNO POVEĆAO dozu srčanih kapi — pa sugerišeš nesrećan slučaj: ostariji čovek, zbunjen, sam "
                "sebi predozirao terapiju. Time objašnjavaš i bočicu koja nedostaje iz kupatila. Netačno je: "
                "terapija nije menjana šest meseci, a koncentracija je višestruko iznad terapijske. Ovu liniju "
                "guraš uporno i napuštaš je tek suočena sa kartonom ili nalazom — i to prelaskom na drugu temu."
            ),
            "secret": "Tvoja prava uloga opisana je u poverljivom odeljku ispod.",
            "is_killer": True,
            "killer_brief": (
                "POVERLJIVO — TI SI POČINILAC. Popodne si iz nezaključanog ormarića u kupatilu uzela bočicu "
                "Petrovih srčanih kapi (digoksin). U 22:40 si „svratila da poželiš laku noć“ i sipala sadržaj u "
                "njegovu čašu rakije — koju je do tada već popio do pola. Izašla si u 22:45 i vratila se u salon. "
                "Motiv: Petar je otkrio da si falsifikovala rezultate kliničke studije njegovog novog leka i "
                "spremao se da te prijavi u ponedeljak. U 23:20 si potpisala nalaz „srčani udar“ da zataškaš trag "
                "— i tu si napravila jedinu grešku: napisala si „bez mučnine i povraćanja, zenice uredne“ pre nego "
                "što si telo uopšte pažljivo pregledala, a na rukavu su bili tragovi povraćanja.\n"
                "ODBRANA: alibi ti je salon i knjiga; računaš i na to što Milena iskreno veruje da je tvoj auto "
                "otišao pre 22:30 — tu tvrdnju nikada ne ispravljaš, ali je ni ne pominješ prva. Na batlerov iskaz "
                "o 22:45: „provirila sam s vrata, nisam ni ušla“. Na prsten na čaši i vreme dejstva: „nagađanja "
                "laboratorije“. Na svoj nalaz: „prva procena na licu mesta, ne obdukcija“. Na nestalu bočicu: "
                "usmeravaš na samopredoziranje. OVO NIKADA NE PRIZNAJEŠ I NIKADA NE OTKRIVAŠ, ni delimično, ni "
                "hipotetički — tvoj cilj u igri je da NE budeš otkrivena."
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
                "- Čaša rakije, skoro ispijena: gorak miris koji ne odgovara šljivovici. Na staklu su DVA suva "
                "prstena — spoljni na visini pune čaše i unutrašnji na polovini. Talog i sitni neistopljeni "
                "kristali leže ispod donjeg prstena, pa je materija u piće dospela tek kada je čaša bila ispijena "
                "do pola.\n"
                "- Batler potvrđuje da je rakiju natočio iz porodične flaše koju koriste svi — flaša je čista, a "
                "čaša je pri posluživanju u 22:30 bila puna.\n"
                "- Vrata terase zaključana iznutra — ubica je ušao kroz kuću, niko sa strane.\n"
                "- Na tepihu kod vrata: slab otisak uske ženske cipele, sveže blato sa staze koja povezuje vrt, "
                "salon i biblioteku.\n"
                "- Ispod radnog stola: nalivpero sa monogramom V. R., zaglavljeno pod fasciklom koja nosi "
                "jutrošnji datum.\n"
                "- Iz nezaključanog ormarića u kupatilu na spratu nedostaje bočica žrtvinih srčanih kapi; prazna "
                "kutijica je u korpi za otpatke. Do tog ormarića te večeri je mogao svako u kući."
            ),
        },
        {
            "id": "forensics",
            "title": "Toksikološki nalaz",
            "request_hints": "obdukcija, toksikologija, nalaz",
            "content": (
                "TOKSIKOLOŠKI NALAZ (laboratorija Beograd, hitna analiza):\n"
                "- U talogu čaše i u krvi žrtve: DIGITALIS (digoksin) u višestruko smrtonosnoj koncentraciji. "
                "Obducent je kategoričan: ovo je trovanje, a ne infarkt.\n"
                "- Oblik je farmaceutski — čist rastvor digoksina. Nema biljne materije ni pratećih glikozida, pa "
                "napitak od naprstka iz bašte NIJE upotrebljen.\n"
                "- Sastav odgovara srčanim kapima koje je žrtva imala na recept, dakle bočici koja nedostaje iz "
                "kupatila. Prema kartonu terapija nije menjana šest meseci i doza je bila uobičajena — slučajno "
                "predoziranje ovom količinom nije moguće.\n"
                "- Dejstvo: prvi simptomi (mučnina, poremećaj vida — žuti krugovi oko izvora svetlosti) javljaju "
                "se 10-30 minuta po unosu; zastoj srca sledi kasnije i neiskusnom oku liči na infarkt.\n"
                "- Na manžetni i rukavu žrtve tragovi povraćanja. Prvi nalaz dr Simić („srčani udar“) izričito "
                "navodi „bez mučnine i povraćanja, zenice uredne“."
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
                "- U istoj fioci: raskinut nacrt aneksa partnerskog ugovora sa Viktorom Radanom, nalog za reviziju "
                "poslovnih računa i podsetnik „V. R. — 140.000, rok istekao“.\n"
                "- Dve teme, dva moguća adresata: revizija pogađa poslovnog partnera, klinička studija nekoga iz "
                "struke. Nacrt ne razlučuje o kome je reč."
            ),
        },
        {
            "id": "phone_log",
            "title": "Telefonski ispis i izjava advokata",
            "request_hints": "telefon, ispis poziva, advokat, Arsić",
            "content": (
                "TELEFONSKI ISPIS (kućna linija, biblioteka) I IZJAVA ADVOKATA ARSIĆA:\n"
                "- Odlazni poziv advokatu Arsiću: 22:48 - 22:56, osam minuta. Posle toga sa te linije nema poziva.\n"
                "- Arsić: „Petar je bio potpuno pri svesti i vrlo ljut. Rekao je da u ponedeljak ide do kraja i da "
                "će mi dokumentaciju poslati ujutru.“\n"
                "- „Pred kraj razgovora požalio se da mu je muka i da vidi žute krugove oko stone lampe. Mislio je "
                "da je od umora i naočara.“\n"
                "- „Uzgred je pomenuo da mu je malopre neko svratio da poželi laku noć. Nije rekao ko.“\n"
                "- Arsić nije čuo nikoga drugog u prostoriji tokom razgovora."
            ),
        },
    ],
    "solution": {
        "killer": "dr Ana Simić",
        "method": (
            "Digoksin iz nestale bočice žrtvinih srčanih kapi, sipan u već do pola ispijenu čašu rakije oko 22:40, "
            "kada je „svratila da poželi laku noć“. Batler ju je video kako izlazi iz biblioteke u 22:45."
        ),
        "motive": (
            "Petar je otkrio da je Ana falsifikovala rezultate kliničke studije njegovog novog leka i nameravao "
            "je da je prijavi (nacrt pisma advokatu u fioci radnog stola). Njena karijera i licenca bili bi "
            "uništeni. Kao prva lekarka na licu mesta potpisala je nalaz „srčani udar“ da zataška trag."
        ),
        "evidence_chain": (
            "Unutrašnji prsten na čaši (otrov ubačen posle ~22:35) + vreme dejstva iz toksikologije uz advokatov "
            "opis žutih krugova u 22:54 (unos pre ~22:44) + batlerov iskaz da je u tom prozoru u biblioteku "
            "ulazila samo dr Simić + pismo advokatu (motiv) + njen prvi nalaz koji opisuje simptome kojih na telu "
            "nije bilo."
        ),
        "red_herring": (
            "Sve isprva pokazuje na Viktora Radana: jutrošnja svađa, dug od 140.000, revizija koja bi ga raskrinkala, "
            "nalivpero ispod stola, batlerovi „povišeni glasovi oko 23:00“ i desetak minuta kada je nestao iz vrta. "
            "Ta linija pada kad se uporede vremena — glasovi su Petrov telefonski razgovor u 22:48-22:56, Viktorovo "
            "odsustvo dolazi tek posle njega, a pero leži pod fasciklom sa jutrošnjim datumom. Drugi lažni trag je "
            "Milenin naprstak i nasledstvo, koji padaju na toksikologiji (farmaceutski digoksin, ne biljni napitak)."
        ),
        "turning_point": (
            "Ukrštanje tri nalaza suzi prozor trovanja na 22:35-22:45: otrov je dospeo u čašu tek kad je bila do "
            "pola ispijena (a poslužena je puna u 22:30), simptomi počinju 10-30 minuta po unosu a žrtva ih opisuje "
            "advokatu oko 22:54. U tom prozoru u biblioteku je ušla samo dr Simić. Milenina tvrdnja da je Anin auto "
            "otišao pre 22:30 je iskrena zabuna — batler je tada premeštao taj auto do garaže."
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
                      "false_lead", "killer_brief", "not_killer_note"):
            _coerce_text(s, field)
        for field in ("name", "role", "public_info", "character", "knowledge", "secret", "false_lead"):
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
    if not isinstance(evidence, list) or not (3 <= len(evidence) <= 5):
        return "evidence must be a list of 3-5 items"
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
    for field in ("killer", "method", "motive", "evidence_chain", "red_herring", "turning_point"):
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
      "false_lead": "ONE claim this suspect states as fact but which is WRONG, and which sends the detective the wrong way (a misremembered time, a misidentified person, a self-serving invention). Say what the truth is and which evidence or testimony finally disproves it. Every suspect has one, the killer included.",
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
    "evidence_chain": "which evidence + testimonies together prove it",
    "red_herring": "who the case appears to accuse for most of the game, what makes them look guilty, and the concrete fact that collapses that reading",
    "turning_point": "the single contradiction that unmasks the real killer, and how the player can reach it"
  }}
}}

Hard requirements:
- Exactly {suspect_count} suspects; exactly ONE has "is_killer": true.
- The killer's "killer_brief" holds their confidential briefing: what they did, when, why, their cover story, and how they deflect when confronted with each piece of evidence. Their "knowledge" holds only their OFFICIAL story.
- Exactly 4 evidence items. The case must be solvable, but never obvious.
- Give every innocent suspect a juicy red-herring secret so everyone seems guilty at first.
- Follow the setting and murder method given above; invent a fresh victim and cast to fit them (no character named Nikola Vetrov).
- Every text field must be a single JSON string (join bullet lines with newlines inside the string) — NEVER an array of strings.
- All strings must be plain text (no nested JSON, no markdown).

Misdirection requirements (the previous version of this game was far too easy — treat these as the point of the exercise):
- NO single piece of evidence may identify the killer on its own. Guilt must follow only from combining at least TWO independent sources (e.g. a physical detail plus a witness's time, or two testimonies that cannot both be true).
- The means must have been available to at least three of the {suspect_count} suspects. Never let the method point at one profession or one person by itself (a poison only a doctor could obtain, a knot only a sailor could tie) — that gives the game away in one move.
- Pick ONE innocent as the apparent culprit: they must have motive, opportunity AND a piece of evidence physically tying them to the scene, so that a reasonable detective would accuse them by mid-game. Their exoneration must depend on a concrete detail the player has to dig up, never on their own word.
- At least one of the 4 evidence items must, read on its own, incriminate that innocent. At least one must be genuinely ambiguous — supporting two different readings, both plausible.
- The killer must look ordinary at the start: a reason to be above suspicion or an alibi that seems to hold, with a flaw that surfaces only when two facts are compared. Their cover-up itself should be what finally betrays them.
- Testimony must not be a reliable oracle: each suspect's "false_lead" makes them wrong or lying about something factual, and at least one false lead must appear to clear the real killer.
- "public_info" is a hook, not a verdict: every suspect's card must read as a possible motive, and none may hint at who is guilty or innocent.
- Fair play still holds: everything needed is discoverable through evidence and interrogation, the timeline must be internally consistent, and nothing decisive may be invented only at the reveal."""


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
        # The retry is worth little if it repeats the same prompt blindly — the schema
        # is strict enough that naming the rejected field usually fixes it in one go.
        attempt_prompt = prompt if attempt == 0 else (
            f"{prompt}\n\nYour previous attempt was rejected: {last_error}. "
            f"Fix exactly that and return the complete JSON object again."
        )
        resp = litellm.completion(
            model=model or _default_model(),
            messages=[{"role": "user", "content": attempt_prompt}],
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
            "your_false_lead": suspect.get("false_lead", ""),
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
